"""AgentRunner — single-user, single-session lifecycle."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_cli.config import load_config
from run_agent import AIAgent

from .media import _rewrite_media
from .state import _STREAMS

# SessionDB import is best-effort — we fall back to in-memory only if the
# import or DB init fails (e.g. db locked).
try:  # pragma: no cover
    from hermes_state import SessionDB  # type: ignore
    _HAS_SESSION_DB = True
except Exception:  # pragma: no cover
    SessionDB = None  # type: ignore
    _HAS_SESSION_DB = False

logger = logging.getLogger(__name__)


class AgentRunner:
    """Owns the lazily-constructed ``AIAgent`` and queues events to SSE.

    All callbacks fire from the worker thread.  They marshal events back
    to the asyncio event loop via ``call_soon_threadsafe`` so the SSE
    handler can ``await queue.get()`` cleanly.
    """

    def __init__(self) -> None:
        self.agent: Optional[AIAgent] = None
        self.conversation_history: List[Dict[str, Any]] = []
        self.session_id: str = ""
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.cancel_flag: threading.Event = threading.Event()
        # Active stream_id (only one in flight at a time for this MVP)
        self.active_stream_id: Optional[str] = None
        self._init_lock = threading.Lock()
        # SessionDB — persistent multi-session storage. Lazily initialised
        # so a missing/locked DB doesn't crash boot. None means we're in
        # ephemeral / in-memory-only mode.
        self._session_db = None  # type: Optional[Any]
        self._session_db_lock = threading.Lock()
        self._session_db_failed = False
        self._clarify_lock = threading.Lock()
        self._clarify_state: Optional[Dict[str, Any]] = None
        # Prefer the latest persisted chat-pwa session on process boot.
        # Only create a fresh session when there is nothing to resume.
        self._bootstrap_initial_session()

    @staticmethod
    def _gen_session_id() -> str:
        """Generate a CLI-style session id (timestamp + short uuid)."""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{ts}_{uuid.uuid4().hex[:6]}"

    # -- SessionDB helpers -------------------------------------------------

    def _ensure_session_db(self):
        """Lazily build a SessionDB instance. Returns None on failure."""
        if not _HAS_SESSION_DB or SessionDB is None:
            return None
        if self._session_db_failed:
            return None
        with self._session_db_lock:
            if self._session_db is not None:
                return self._session_db
            try:
                self._session_db = SessionDB()
                return self._session_db
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "SessionDB unavailable — chat sessions won't persist: %s", e
                )
                self._session_db_failed = True
                return None

    def _bootstrap_initial_session(self) -> None:
        """Resume the latest chat-pwa session, else create a fresh one."""
        self.bind_latest_session_or_create()

    def _bind_latest_persisted_session(self) -> Optional[Dict[str, Any]]:
        """Best-effort: bind to the latest persisted chat-pwa session."""
        db = self._ensure_session_db()
        if db is None:
            return None
        try:
            rows = db.list_sessions_rich(
                source="chat-pwa",
                limit=1,
                include_children=False,
            ) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("list_sessions_rich bootstrap failed: %s", e)
            return None
        for row in rows:
            sid = str(row.get("id") or "").strip()
            if not sid:
                continue
            try:
                return self.bind_session(sid)
            except KeyError:
                logger.warning(
                    "latest chat-pwa session disappeared before bootstrap bind: %s",
                    sid,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("bootstrap bind_session failed for %s: %s", sid, e)
        return None

    def bind_latest_session_or_create(self) -> Tuple[Dict[str, Any], bool]:
        """Bind to the latest persisted session, else create a fresh one.

        Returns ``(session_meta, created_new)``.
        """
        meta = self._bind_latest_persisted_session()
        if meta is not None:
            return meta, False
        new_id = self.start_new_session()
        return {"id": new_id}, True

    def bind_session(self, session_id: str) -> Dict[str, Any]:
        """Switch the runner to ``session_id``: load history, clear stream state.

        Returns the session metadata dict (or a minimal dict when SessionDB
        is unavailable). Raises ``KeyError`` if the id is unknown to a
        live SessionDB.
        """
        db = self._ensure_session_db()
        meta: Optional[Dict[str, Any]] = None
        history: List[Dict[str, Any]] = []
        if db is not None:
            try:
                meta = db.get_session(session_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("get_session failed for %s: %s", session_id, e)
                meta = None
            if meta is None:
                raise KeyError(session_id)
            try:
                history = db.get_messages_as_conversation(session_id) or []
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "get_messages_as_conversation failed for %s: %s", session_id, e
                )
                history = []

        self.session_id = session_id
        self.conversation_history = list(history)
        # Clear any in-flight stream state — we don't carry deltas across
        # session boundaries.
        self.active_stream_id = None
        self.cancel_flag = threading.Event()
        self._clear_pending_clarify()
        # Force the agent to be rebuilt so its internal session_id matches.
        self.agent = None
        return meta or {"id": session_id}

    def start_new_session(self) -> str:
        """Create a fresh SessionDB row, bind to it, return the new id."""
        new_id = self._gen_session_id()
        db = self._ensure_session_db()
        if db is not None:
            try:
                db.create_session(session_id=new_id, source="chat-pwa")
            except Exception as e:  # noqa: BLE001
                logger.warning("create_session failed for %s: %s", new_id, e)

        self.session_id = new_id
        self.conversation_history = []
        self.active_stream_id = None
        self.cancel_flag = threading.Event()
        self._clear_pending_clarify()
        self.agent = None
        return new_id

    def _clear_pending_clarify(self, request_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Drop the active clarify prompt, optionally matching on id."""
        with self._clarify_lock:
            state = self._clarify_state
            if state is None:
                return None
            if request_id is not None and state.get("id") != request_id:
                return None
            self._clarify_state = None
            return state

    def resolve_clarify(self, request_id: str, answer: str) -> bool:
        """Answer the currently pending clarify prompt."""
        with self._clarify_lock:
            state = self._clarify_state
            if state is None or state.get("id") != request_id:
                return False
            response_queue = state.get("response_queue")
        try:
            response_queue.put_nowait(answer)
            return True
        except Exception:
            return False

    def abort_pending_clarify(self, reason: Optional[str] = None) -> bool:
        """Unblock the agent if it is waiting on a clarify prompt."""
        with self._clarify_lock:
            state = self._clarify_state
            if state is None:
                return False
            response_queue = state.get("response_queue")
        fallback = (
            reason
            or "The clarify prompt was cancelled. Use your best judgement and continue."
        )
        try:
            response_queue.put_nowait(fallback)
            return True
        except Exception:
            return False

    def interrupt_active_turn(self, final_text: str = "(cancelled)") -> bool:
        """Best-effort interrupt of the currently active turn."""
        stream_id = self.active_stream_id
        if not stream_id:
            return False
        self.cancel_flag.set()
        self.abort_pending_clarify(
            "The clarify prompt was cancelled because the turn was stopped. "
            "Use your best judgement and continue."
        )
        if self.agent is not None:
            try:
                self.agent.interrupt()
            except Exception:
                logger.debug("agent.interrupt() failed", exc_info=True)
        q = _STREAMS.get(stream_id)
        if q is not None:
            try:
                q.put_nowait({"type": "done", "final_text": final_text})
            except Exception:
                pass
        return True

    def wait_for_idle(self, timeout: float = 15.0) -> bool:
        """Wait until the current worker exits and runner state is idle."""
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            if self.active_stream_id is None:
                worker = self.worker_thread
                if worker is None or not worker.is_alive():
                    return True
            time.sleep(0.05)
        worker = self.worker_thread
        return self.active_stream_id is None and (worker is None or not worker.is_alive())

    # -- agent construction ------------------------------------------------

    def _ensure_agent(self) -> AIAgent:
        with self._init_lock:
            if self.agent is not None:
                return self.agent

            cfg = load_config()
            model_cfg = cfg.get("model") or {}
            if not isinstance(model_cfg, dict):
                raise RuntimeError(
                    "config.yaml has 'model' as a non-dict; expected keys "
                    "model.provider/model.base_url/model.api_key/model.default."
                )
            model = (model_cfg.get("default") or "").strip()
            provider = (model_cfg.get("provider") or "").strip() or None
            base_url = (model_cfg.get("base_url") or "").strip() or None
            api_key = (model_cfg.get("api_key") or "").strip() or None

            if not model:
                raise RuntimeError(
                    "No model configured.  Set model.default in "
                    "~/.hermes/config.yaml or run 'hermes setup'."
                )

            self.agent = AIAgent(
                model=model,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                session_id=self.session_id,
                platform="chat-pwa",
                quiet_mode=True,
                stream_delta_callback=self._on_stream_delta,
                reasoning_callback=self._on_reasoning,
                thinking_callback=self._on_thinking,
                tool_gen_callback=self._on_tool_gen,
                tool_start_callback=self._on_tool_start,
                tool_complete_callback=self._on_tool_complete,
                tool_progress_callback=self._on_tool_progress,
                interim_assistant_callback=self._on_interim_assistant,
                clarify_callback=self._on_clarify,
                status_callback=self._on_status,
            )
            logger.info(
                "AgentRunner: agent ready (session=%s model=%s provider=%s)",
                self.session_id, model, provider or "auto",
            )
            return self.agent

    # -- queue plumbing ----------------------------------------------------

    def _emit(self, event: Dict[str, Any]) -> None:
        """Push an event to the active stream's queue (thread-safe)."""
        if self.cancel_flag.is_set():
            return
        sid = self.active_stream_id
        if not sid:
            return
        q = _STREAMS.get(sid)
        if q is None or self.loop is None:
            return
        try:
            self.loop.call_soon_threadsafe(q.put_nowait, event)
        except RuntimeError:
            # loop closed
            pass

    # -- agent callbacks ---------------------------------------------------

    def _on_stream_delta(self, text: Optional[str]) -> None:
        if text is None or text == "":
            self._emit({"type": "turn_boundary"})
        else:
            self._emit({"type": "text_delta", "text": text})

    def _on_reasoning(self, text: str) -> None:
        if text:
            self._emit({"type": "thinking_delta", "text": text})

    def _on_thinking(self, text: str) -> None:
        if text:
            self._emit({"type": "status", "category": "thinking", "text": text})

    def _on_tool_gen(self, name: str) -> None:
        self._emit({"type": "tool_call_start", "name": name})

    def _on_tool_start(self, *cb_args: Any) -> None:
        # AIAgent calls this as (tool_id, name, args).  Spec asks for
        # (name, args) — accept either shape.
        if len(cb_args) >= 3:
            _id, name, args = cb_args[0], cb_args[1], cb_args[2]
        elif len(cb_args) == 2:
            name, args = cb_args
        else:
            name, args = (cb_args[0] if cb_args else "?"), None
        self._emit({"type": "tool_call", "name": str(name), "args": args})

    def _on_tool_complete(self, *cb_args: Any) -> None:
        # AIAgent calls this as (tool_id, name, args, result).
        if len(cb_args) >= 4:
            _id, name, _args, result = cb_args[0], cb_args[1], cb_args[2], cb_args[3]
        elif len(cb_args) == 2:
            name, result = cb_args
        else:
            name = cb_args[1] if len(cb_args) > 1 else "?"
            result = cb_args[-1] if cb_args else ""
        self._emit({
            "type": "tool_result",
            "name": str(name),
            "result": str(result)[:5000],
        })

    def _on_tool_progress(
        self,
        event_type: str,
        name: Optional[str] = None,
        preview: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        **meta: Any,
    ) -> None:
        self._emit({
            "type": "tool_progress",
            "event": str(event_type or ""),
            "name": str(name or ""),
            "preview": preview,
            "args": args,
            "duration": meta.get("duration"),
            "is_error": bool(meta.get("is_error", False)),
        })

    def _on_interim_assistant(self, text: str, *, already_streamed: bool = False) -> None:
        if text:
            self._emit({
                "type": "assistant_interim",
                "text": text,
                "already_streamed": bool(already_streamed),
            })

    def _on_clarify(self, question: str, choices: Optional[List[str]] = None) -> str:
        timeout = 120
        response_queue: "queue.Queue[str]" = queue.Queue()
        request_id = uuid.uuid4().hex
        state = {
            "id": request_id,
            "question": question,
            "choices": list(choices or []),
            "response_queue": response_queue,
        }
        with self._clarify_lock:
            self._clarify_state = state
        self._emit({
            "type": "clarify_request",
            "id": request_id,
            "question": question,
            "choices": list(choices or []),
        })
        deadline = time.monotonic() + timeout
        fallback = (
            "The user did not provide a response within the time limit. "
            "Use your best judgement to make the choice and proceed."
        )
        try:
            while True:
                try:
                    answer = response_queue.get(timeout=1)
                    return str(answer).strip() or fallback
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        return fallback
        finally:
            self._clear_pending_clarify(request_id)
            self._emit({"type": "clarify_done", "id": request_id})

    def _on_status(self, category: str, msg: str) -> None:
        self._emit({"type": "status", "category": str(category), "text": str(msg)})

    # -- worker entry ------------------------------------------------------

    def run_turn(self, stream_id: str, user_text: str) -> None:
        """Run a single conversation turn in this thread."""
        self.active_stream_id = stream_id
        self.cancel_flag.clear()
        prev_len = len(self.conversation_history)
        try:
            agent = self._ensure_agent()
            result = agent.run_conversation(
                user_text,
                conversation_history=list(self.conversation_history),
            )
            final = (result or {}).get("final_response") or ""
            # Update history for the next turn.  The agent returns
            # "messages" today; keep "conversation_history" as the
            # primary key per spec but fall back to "messages".
            new_history = (
                (result or {}).get("conversation_history")
                or (result or {}).get("messages")
                or []
            )
            if new_history:
                self.conversation_history = list(new_history)
                # Persist whatever the agent appended this turn so the
                # session survives restarts and ``/api/sessions/switch``
                # can replay it on next load.
                self._persist_new_messages(new_history[prev_len:])

            # MEDIA: rewrite — copy referenced files into our uploads dir
            # under a fresh token and rewrite to /files/<token>.
            rewritten = _rewrite_media(final, self.session_id)
            self._emit({"type": "done", "final_text": rewritten})
        except Exception as e:  # noqa: BLE001
            logger.exception("AgentRunner.run_turn failed")
            self._emit({"type": "error", "message": str(e)})
        finally:
            self.active_stream_id = None

    def _persist_new_messages(self, new_msgs: List[Dict[str, Any]]) -> None:
        """Append turn-new messages to SessionDB, mirroring gateway/cli flows."""
        db = self._ensure_session_db()
        if db is None or not new_msgs:
            return
        for msg in new_msgs:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if not role:
                continue
            content = msg.get("content")
            if isinstance(content, list):
                # OpenAI-style multi-part content; flatten text parts so
                # SessionDB.get_messages_as_conversation has a string to hand
                # back. Image parts survive as the agent's MEDIA:/files
                # references in the assistant reply so we don't lose them.
                parts = [
                    p.get("text")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
                ]
                content_str: Optional[str] = "\n".join(parts) if parts else None
            elif isinstance(content, str):
                content_str = content
            else:
                content_str = None
            try:
                db.append_message(
                    session_id=self.session_id,
                    role=str(role),
                    content=content_str,
                    tool_calls=msg.get("tool_calls"),
                    tool_name=msg.get("name") or msg.get("tool_name"),
                    tool_call_id=msg.get("tool_call_id"),
                    finish_reason=msg.get("finish_reason"),
                    reasoning_content=msg.get("reasoning_content") or msg.get("reasoning"),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "append_message failed for %s/%s: %s",
                    self.session_id, role, e,
                )


_runner = AgentRunner()


def get_runner() -> AgentRunner:
    """Return the singleton AgentRunner instance."""
    return _runner
