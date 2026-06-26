import { marked } from 'marked';
import hljs from 'highlight.js/lib/core';
// Register commonly used languages
import javascript from 'highlight.js/lib/languages/javascript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import yaml from 'highlight.js/lib/languages/yaml';
import markdown from 'highlight.js/lib/languages/markdown';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import typescript from 'highlight.js/lib/languages/typescript';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import java from 'highlight.js/lib/languages/java';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import sql from 'highlight.js/lib/languages/sql';
import diff from 'highlight.js/lib/languages/diff';

hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('python', python);
hljs.registerLanguage('py', python);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', bash);
hljs.registerLanguage('json', json);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('rs', rust);
hljs.registerLanguage('go', go);
hljs.registerLanguage('java', java);
hljs.registerLanguage('c', c);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('diff', diff);

// Hermes chat PWA frontend. Plain JS, no framework. Drives /api/* JSON + SSE.
// Pairs with Tailwind CDN markup in index.html and animation-only style.css.
(() => {
  'use strict';

  // Auth is now cookie-based: the browser auto-sends the hermes_session
  // cookie on every same-origin fetch (with credentials: 'same-origin' /
  // 'include'). The legacy <meta name="hermes-token"> is still read for
  // installed PWAs that bootstrapped from a ?token= URL — when present we
  // fall back to attaching it as a Bearer header so older shells keep
  // working until they reload.
  const tokenMeta = document.querySelector('meta[name="hermes-token"]');
  const LEGACY_TOKEN = (tokenMeta && tokenMeta.content && !tokenMeta.content.includes('HERMES_TOKEN'))
    ? tokenMeta.content.trim() : '';

  function gotUnauthorized() {
    // Cookie expired or invalid — bounce to login.
    location.replace('/');
  }

  const $ = (id) => document.getElementById(id);
  const messagesEl = $('messages'), scrollEl = $('chat-scroll'),
        composer = $('composer'), input = $('composer-input'),
        attachBtn = $('attach-btn'), fileInput = $('file-input'),
        attachmentsEl = $('attachments'),
        clarifyBox = $('clarify-box'), clarifyQuestion = $('clarify-question'),
        clarifyChoices = $('clarify-choices'), slashPop = $('slash-popdown'),
        statusPill = $('status-pill'), infoBtn = $('info-btn'),
        popover = $('model-popover'), jumpLatest = $('jump-latest'),
        sendBtn = $('send-btn'), stopBtn = $('stop-btn');

  const escapeHtml = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const authHeaders = (extra) => {
    const h = Object.assign({}, extra || {});
    if (LEGACY_TOKEN) h['Authorization'] = 'Bearer ' + LEGACY_TOKEN;
    return h;
  };
  async function apiJson(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      credentials: 'same-origin',
      body: JSON.stringify(body || {}),
    });
    if (res.status === 401) { gotUnauthorized(); throw new Error('unauthorized'); }
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return res.json();
  }

  // data-hidden toggle helpers (matches Tailwind data-[hidden=true]:hidden)
  const show = (el) => el.setAttribute('data-hidden', 'false');
  const hide = (el) => el.setAttribute('data-hidden', 'true');
  const isHidden = (el) => el.getAttribute('data-hidden') !== 'false';

  const state = {
    streamId: null, es: null, retried: false,
    current: null, attachments: [], autoScroll: true,
    clarify: null, clarifySubmitting: false,
  };

  // ---------- Status + scroll ----------
  // Status pill colors mapped to data-status; we set both attribute and class for color.
  const STATUS_CLASSES = {
    idle:      'text-zinc-400',
    thinking:  'text-amber-400',
    streaming: 'text-accent-400',
    error:     'text-red-400',
  };
  function setStatus(kind) {
    statusPill.setAttribute('data-status', kind);
    statusPill.textContent = kind;
    // Strip prior color classes, add the new one
    statusPill.className = statusPill.className.replace(/\btext-\S+/g, '').trim() + ' ' + (STATUS_CLASSES[kind] || 'text-zinc-400');
    // re-trigger pill slide animation
    statusPill.removeAttribute('data-anim');
    void statusPill.offsetWidth; // reflow
    statusPill.setAttribute('data-anim', 'pill-in');
    // Toggle send / stop buttons
    const busy = kind === 'thinking' || kind === 'streaming';
    if (busy) { hide(sendBtn); show(stopBtn); }
    else      { show(sendBtn); hide(stopBtn); }
  }
  const isNearBottom = () => scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 80;
  const scrollBottom = (force) => {
    if (force || state.autoScroll) requestAnimationFrame(() => { scrollEl.scrollTop = scrollEl.scrollHeight; });
  };
  scrollEl.addEventListener('scroll', () => {
    state.autoScroll = isNearBottom();
    if (state.autoScroll) hide(jumpLatest); else show(jumpLatest);
  });
  jumpLatest.addEventListener('click', () => { state.autoScroll = true; scrollBottom(true); hide(jumpLatest); });

  // ---------- Markdown ----------
  function renderMarkdown(text) {
    if (!text) return '';
    marked.setOptions({
      breaks: true,
      gfm: true,
      highlight: function(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
          try { return hljs.highlight(code, { language: lang }).value; }
          catch (_) {}
        }
        try { return hljs.highlightAuto(code).value; }
        catch (_) {}
        return code;
      },
    });
    return marked.parse(text);
  }
  // Cookie auth covers same-origin GETs (EventSource and <img>) automatically,
  // so /files/<token> URLs no longer need a query suffix. Keep the helpers as
  // pass-throughs for back-compat with any callers still using them.
  const withTokenQuery = (url) => url;
  const injectFileTokens = (html) => html;
  function promoteImageLinks(container) {
    container.querySelectorAll('a[href*="/files/"]').forEach((a) => {
      if (a.querySelector('img')) return;
      const img = document.createElement('img');
      img.src = a.getAttribute('href');
      img.alt = a.textContent || 'image';
      img.className = 'rounded-lg max-w-full my-2';
      a.replaceWith(img);
    });
  }

  // ---------- Messages ----------
  // Full-width row with role label on the left and a content stack on the right.
  // No bubbles for user messages — clean Linear/Claude.ai feel.
  function makeMessage(role) {
    const wrap = document.createElement('div');
    wrap.setAttribute('data-anim', 'msg-in');
    wrap.className = 'flex gap-4 sm:gap-5';
    wrap.dataset.role = role;
    const label = document.createElement('div');
    label.className = 'shrink-0 w-12 sm:w-14 pt-0.5 text-[11px] uppercase tracking-wider font-medium text-zinc-500 select-none';
    label.textContent = role === 'user' ? 'You' : 'Hermes';
    wrap.appendChild(label);
    const body = document.createElement('div');
    body.className = 'flex-1 min-w-0 flex flex-col gap-2';
    wrap.appendChild(body);
    wrap._body = body;
    return wrap;
  }
  function userTextEl() {
    const t = document.createElement('div');
    // No bubble — just clean text matching the assistant's typographic rhythm.
    t.className = 'text-zinc-900 text-[15px] leading-7 whitespace-pre-wrap break-words';
    return t;
  }
  function assistantTextEl() {
    const t = document.createElement('div');
    t.className = 'prose prose-sm max-w-none text-zinc-900 leading-7 break-words';
    return t;
  }
  function appendUserMessage(text, attachmentUrls) {
    const el = makeMessage('user');
    if (attachmentUrls && attachmentUrls.length) {
      const row = document.createElement('div');
      row.className = 'flex flex-wrap gap-2';
      attachmentUrls.forEach((u) => {
        const img = document.createElement('img');
        img.src = u;
        img.className = 'rounded-md max-w-[220px] border border-zinc-200';
        row.appendChild(img);
      });
      el._body.appendChild(row);
    }
    if (text) {
      const t = userTextEl();
      t.textContent = text;
      el._body.appendChild(t);
    }
    messagesEl.appendChild(el);
    scrollBottom(true);
  }
  function appendAssistantStatic(text) {
    const wrap = makeMessage('assistant');
    const textEl = assistantTextEl();
    textEl.innerHTML = injectFileTokens(renderMarkdown(text || ''));
    promoteImageLinks(textEl);
    wrap._body.appendChild(textEl);
    messagesEl.appendChild(wrap);
    scrollBottom();
    return wrap;
  }
  function startAssistantMessage() {
    const wrap = makeMessage('assistant');
    const textEl = assistantTextEl();
    wrap._body.appendChild(textEl);
    messagesEl.appendChild(wrap);
    state.current = {
      msgEl: wrap, bodyEl: wrap._body, textEl, textBuf: '',
      thinkEl: null, thinkBuf: '',
      toolMap: new Map(), pendingPlaceholders: [],
      statusEl: null, cursorEl: null,
    };
    addCursor();
    scrollBottom();
    return state.current;
  }
  function currentHasVisibleContent(cur) {
    if (!cur) return false;
    return !!(
      (cur.textBuf && cur.textBuf.trim()) ||
      (cur.thinkBuf && cur.thinkBuf.trim()) ||
      cur.toolMap.size ||
      cur.pendingPlaceholders.length ||
      (cur.statusEl && cur.statusEl.dataset.transient !== '1' && (cur.statusEl.textContent || '').trim())
    );
  }
  function discardCurrentIfEmpty() {
    const cur = state.current;
    if (!cur || currentHasVisibleContent(cur)) return false;
    if (cur.msgEl && cur.msgEl.parentNode) cur.msgEl.remove();
    state.current = null;
    return true;
  }
  function finalizeCurrentMessage(clearState) {
    const cur = state.current;
    if (!cur) return;
    if (cur.cursorEl) {
      cur.cursorEl.remove();
      cur.cursorEl = null;
    }
    cur.textEl.innerHTML = injectFileTokens(renderMarkdown(cur.textBuf || ''));
    promoteImageLinks(cur.textEl);
    clearTransientStatus();
    if (clearState) state.current = null;
    scrollBottom();
  }
  function addCursor() {
    if (!state.current || state.current.cursorEl) return;
    const c = document.createElement('span');
    c.setAttribute('data-cursor', '1');
    c.textContent = '▍';
    state.current.textEl.appendChild(c);
    state.current.cursorEl = c;
  }
  function removeCursor(fade) {
    if (!state.current || !state.current.cursorEl) return;
    const c = state.current.cursorEl;
    if (fade) { c.setAttribute('data-fade', '1'); setTimeout(() => c.remove(), 320); }
    else c.remove();
    state.current.cursorEl = null;
  }
  function rerenderAssistantText() {
    const cur = state.current; if (!cur) return;
    const had = !!cur.cursorEl;
    if (cur.cursorEl) { cur.cursorEl.remove(); cur.cursorEl = null; }
    cur.textEl.innerHTML = renderMarkdown(cur.textBuf);
    if (had) addCursor();
    scrollBottom();
  }
  function ensureThinkingBlock() {
    if (!state.current) return null;
    if (state.current.thinkEl) return state.current.thinkEl;
    const block = document.createElement('div');
    block.className = 'rounded-md border-l-2 border-accent/60 bg-zinc-50 px-3 py-2 text-zinc-600 font-mono text-[12px]';
    block.dataset.open = '0';
    block.innerHTML = `
      <button type="button" class="thinking-header inline-flex items-center gap-1.5 italic cursor-pointer select-none text-zinc-400">
        <span data-caret>▶</span><span>thinking</span>
      </button>
      <div data-collapsible class="thinking-body whitespace-pre-wrap mt-1"></div>`;
    block.querySelector('.thinking-header').addEventListener('click', () => {
      const next = block.dataset.open === '1' ? '0' : '1';
      block.dataset.open = next;
      block.querySelector('[data-collapsible]').dataset.open = next;
    });
    state.current.bodyEl.insertBefore(block, state.current.textEl);
    state.current.thinkEl = block;
    return block;
  }
  function appendThinking(delta) {
    const b = ensureThinkingBlock(); if (!b) return;
    state.current.thinkBuf += delta;
    b.querySelector('.thinking-body').textContent = state.current.thinkBuf;
    scrollBottom();
  }

  // ---------- Tool calls ----------
  const safeJson = (v) => { try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); } };
  const DOT_CLASS = {
    running: 'bg-amber-400',
    done:    'bg-emerald-400',
    error:   'bg-red-400',
    idle:    'bg-zinc-400',
  };
  function makeToolBlock(name, status) {
    const el = document.createElement('div');
    el.className = 'rounded-md border-l-2 border-accent/60 bg-zinc-50 text-[12px] overflow-hidden';
    el.dataset.open = '0';
    el.innerHTML = `
      <button type="button" class="tool-header w-full flex items-center gap-2 px-3 py-2 cursor-pointer select-none text-left hover:bg-zinc-100">
        <span data-tool-dot="${status}" class="inline-block w-2 h-2 rounded-full shrink-0 ${DOT_CLASS[status] || DOT_CLASS.idle}"></span>
        <span class="min-w-0 flex-1 flex items-center gap-2">
          <span class="tool-name font-mono text-zinc-900 shrink-0">${escapeHtml(name)}</span>
          <span class="tool-preview min-w-0 truncate text-zinc-500"></span>
        </span>
        <span class="tool-meta shrink-0 text-[10px] uppercase tracking-wider text-zinc-400"></span>
        <span class="ml-auto text-zinc-500" data-caret>▶</span>
      </button>
      <div data-collapsible class="tool-body"></div>`;
    el.querySelector('.tool-header').addEventListener('click', () => {
      const next = el.dataset.open === '1' ? '0' : '1';
      el.dataset.open = next;
      el.querySelector('[data-collapsible]').dataset.open = next;
    });
    return el;
  }
  function setToolDot(el, status) {
    const dot = el.querySelector('[data-tool-dot]');
    if (!dot) return;
    dot.setAttribute('data-tool-dot', status);
    dot.className = 'inline-block w-2 h-2 rounded-full shrink-0 ' + (DOT_CLASS[status] || DOT_CLASS.idle);
  }
  function setToolPreview(el, preview) {
    const slot = el.querySelector('.tool-preview');
    if (!slot) return;
    slot.textContent = preview || '';
    slot.classList.toggle('hidden', !preview);
  }
  function setToolMeta(el, text, isError) {
    const slot = el.querySelector('.tool-meta');
    if (!slot) return;
    slot.textContent = text || '';
    slot.className = 'tool-meta shrink-0 text-[10px] uppercase tracking-wider ' + (isError ? 'text-red-500' : 'text-zinc-400');
  }
  const placeBeforeText = (n) => state.current && state.current.bodyEl.insertBefore(n, state.current.textEl);

  function onToolStart(name, preview) {
    const el = makeToolBlock(name, 'running');
    setToolPreview(el, preview || '');
    placeBeforeText(el);
    state.current.pendingPlaceholders.push({ name, el });
    scrollBottom();
  }
  function onToolCall(name, args) {
    let entry;
    const idx = state.current.pendingPlaceholders.findIndex((p) => p.name === name);
    if (idx >= 0) entry = state.current.pendingPlaceholders.splice(idx, 1)[0];
    else { const el = makeToolBlock(name, 'running'); placeBeforeText(el); entry = { name, el }; }
    const sec = document.createElement('div');
    sec.className = 'tool-section args border-t border-zinc-200 px-3 py-2';
    sec.innerHTML = '<div class="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">args</div><pre class="font-mono text-[12px] text-zinc-800 whitespace-pre-wrap break-words m-0"></pre>';
    sec.querySelector('pre').textContent = (typeof args === 'string') ? args : safeJson(args);
    entry.el.querySelector('.tool-body').appendChild(sec);
    let arr = state.current.toolMap.get(name);
    if (!arr) { arr = []; state.current.toolMap.set(name, arr); }
    arr.push(entry.el);
    scrollBottom();
  }
  function onToolResult(name, result) {
    const arr = state.current.toolMap.get(name) || [];
    let target = arr.find((el) => !el.querySelector('.tool-section.result')) || arr[arr.length - 1];
    if (!target) { target = makeToolBlock(name, 'done'); placeBeforeText(target); }
    setToolDot(target, 'done');
    const sec = document.createElement('div');
    sec.className = 'tool-section result border-t border-zinc-200 px-3 py-2';
    sec.innerHTML = '<div class="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">result</div><pre class="font-mono text-[12px] text-zinc-800 whitespace-pre-wrap break-words m-0"></pre>';
    sec.querySelector('pre').textContent = (typeof result === 'string') ? result : safeJson(result);
    target.querySelector('.tool-body').appendChild(sec);
    scrollBottom();
  }
  function onToolProgress(eventName, name, preview, args, duration, isError) {
    if (!state.current) return;
    if (eventName === 'tool.started') {
      const pending = state.current.pendingPlaceholders.find((p) => p.name === name);
      if (pending) setToolPreview(pending.el, preview || '');
      else onToolStart(name, preview || '');
      return;
    }
    if (eventName === 'tool.completed') {
      const arr = state.current.toolMap.get(name) || [];
      let target = arr.find((el) => !el.querySelector('.tool-section.result')) || arr[arr.length - 1];
      if (!target) {
        const pending = state.current.pendingPlaceholders.find((p) => p.name === name);
        target = pending ? pending.el : makeToolBlock(name, isError ? 'error' : 'done');
        if (!pending) placeBeforeText(target);
      }
      setToolDot(target, isError ? 'error' : 'done');
      if (preview) setToolPreview(target, preview);
      if (duration != null) {
        const label = `${Number(duration).toFixed(1)}s${isError ? ' error' : ''}`;
        setToolMeta(target, label, !!isError);
      } else if (isError) {
        setToolMeta(target, 'error', true);
      }
      scrollBottom();
      return;
    }
    if (eventName === 'reasoning.available' && preview && (!state.current.thinkBuf || !state.current.thinkBuf.trim())) {
      appendThinking(preview);
    }
  }

  // ---------- Status / errors ----------
  function showStatusLine(category, text) {
    if (!state.current) return;
    if (state.current.statusEl && state.current.statusEl.dataset.transient === '1') state.current.statusEl.remove();
    const line = document.createElement('div');
    line.setAttribute('data-anim', 'msg-in');
    line.className = 'text-[12px] text-zinc-500 px-1 flex items-center gap-2';
    if (category === 'thinking') {
      line.classList.add('italic', 'text-zinc-400');
      line.dataset.transient = '1';
      line.innerHTML = `<span data-dots><span></span><span></span><span></span></span>${escapeHtml(text || 'thinking…')}`;
    } else {
      line.textContent = text || category;
    }
    state.current.bodyEl.insertBefore(line, state.current.textEl);
    state.current.statusEl = line;
    scrollBottom();
  }
  function clearTransientStatus() {
    if (state.current && state.current.statusEl && state.current.statusEl.dataset.transient === '1') {
      state.current.statusEl.remove();
      state.current.statusEl = null;
    }
  }
  function showErrorBlock(message) {
    let target;
    if (state.current) target = state.current.bodyEl;
    else { const m = makeMessage('assistant'); messagesEl.appendChild(m); target = m._body; }
    const eb = document.createElement('div');
    eb.setAttribute('data-anim', 'msg-in');
    eb.className = 'border border-red-900/60 bg-red-950/40 text-red-200 px-3 py-2 rounded-md text-[13px] whitespace-pre-wrap';
    eb.textContent = message || 'Error';
    target.appendChild(eb);
    scrollBottom();
  }
  function setClarifyState(next) {
    state.clarify = next;
    if (!clarifyBox) return;
    if (!next) {
      hide(clarifyBox);
      clarifyQuestion.textContent = '';
      clarifyChoices.innerHTML = '';
      attachBtn.disabled = false;
      input.placeholder = 'Message Hermes…';
      return;
    }
    clarifyQuestion.textContent = next.question || '';
    clarifyChoices.innerHTML = '';
    hideSlash();
    (next.choices || []).forEach((choice) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'inline-flex items-center rounded-full border border-amber-300 bg-white px-3 py-1.5 text-[12px] text-zinc-800 hover:bg-amber-50 transition-colors';
      btn.textContent = choice;
      btn.addEventListener('click', () => submitClarifyAnswer(choice));
      clarifyChoices.appendChild(btn);
    });
    show(clarifyBox);
    attachBtn.disabled = true;
    input.placeholder = 'Answer Hermes…';
    input.focus();
    scrollBottom();
  }
  async function submitClarifyAnswer(answer) {
    const prompt = state.clarify;
    const text = String(answer == null ? '' : answer).trim();
    if (!prompt || !text || state.clarifySubmitting) return;
    state.clarifySubmitting = true;
    try {
      await apiJson('/api/clarify/respond', { id: prompt.id, answer: text });
      appendUserMessage(text, []);
      input.value = '';
      autosizeInput();
      clearAttachments();
      hideSlash();
      setClarifyState(null);
      setStatus('thinking');
    } catch (err) {
      showErrorBlock('Failed to answer prompt: ' + (err && err.message || err));
    } finally {
      state.clarifySubmitting = false;
    }
  }
  function onAssistantInterim(text, alreadyStreamed) {
    if (!text) return;
    if (alreadyStreamed) {
      if (state.current) finalizeCurrentMessage(true);
      return;
    }
    if (state.current) {
      if (!discardCurrentIfEmpty()) finalizeCurrentMessage(true);
    }
    appendAssistantStatic(text);
  }

  // ---------- SSE ----------
  function openStream(streamId) {
    state.streamId = streamId;
    const url = `/api/stream/${encodeURIComponent(streamId)}`;
    // EventSource sends cookies on same-origin requests by default, so the
    // hermes_session cookie authenticates the stream. withCredentials forces
    // it even when callers serve over a different proto/port mix.
    const es = new EventSource(url, { withCredentials: true });
    state.es = es;
    setStatus('streaming');

    const handler = (type) => (ev) => {
      let data = {}; try { data = JSON.parse(ev.data); } catch (_) {}
      handleEvent(type, data);
    };
    ['text_delta','thinking_delta','tool_call_start','tool_call','tool_result','tool_progress','assistant_interim','clarify_request','clarify_done','turn_boundary','status','done','error']
      .forEach((t) => es.addEventListener(t, handler(t)));
    es.onmessage = (ev) => {
      try { const data = JSON.parse(ev.data); if (data && data.type) handleEvent(data.type, data); } catch (_) {}
    };
    // Single retry with 1s backoff if stream errors mid-turn.
    es.onerror = () => {
      es.close(); state.es = null;
      if (!state.current) { setStatus('idle'); return; }
      if (state.retried) { showErrorBlock('Stream connection lost.'); finishTurn(); return; }
      state.retried = true;
      setStatus('thinking');
      setTimeout(() => { if (state.streamId) openStream(state.streamId); }, 1000);
    };
  }
  function handleEvent(type, data) {
    if (!state.current && !['error', 'assistant_interim', 'clarify_request', 'clarify_done', 'turn_boundary'].includes(type)) startAssistantMessage();
    switch (type) {
      case 'text_delta':
        clearTransientStatus();
        state.current.textBuf += (data.text || '');
        rerenderAssistantText();
        setStatus('streaming');
        break;
      case 'thinking_delta':
        appendThinking(data.text || '');
        setStatus('thinking');
        break;
      case 'tool_call_start': onToolStart(data.name || 'tool'); break;
      case 'tool_call':       onToolCall(data.name || 'tool', data.args); break;
      case 'tool_result':     onToolResult(data.name || 'tool', data.result); break;
      case 'tool_progress':
        onToolProgress(data.event || '', data.name || 'tool', data.preview, data.args, data.duration, !!data.is_error);
        break;
      case 'assistant_interim':
        onAssistantInterim(data.text || '', !!data.already_streamed);
        break;
      case 'clarify_request':
        setClarifyState({ id: data.id || '', question: data.question || '', choices: Array.isArray(data.choices) ? data.choices : [] });
        setStatus('thinking');
        break;
      case 'clarify_done':
        if (state.clarify && (!data.id || data.id === state.clarify.id)) setClarifyState(null);
        break;
      case 'turn_boundary':
        if (state.current) finalizeCurrentMessage(true);
        break;
      case 'status':
        showStatusLine(data.category || '', data.text || '');
        if (data.category === 'thinking') setStatus('thinking');
        break;
      case 'done': {
        if (!state.current) {
          if (data.final_text) appendAssistantStatic(data.final_text);
          finishTurn();
          break;
        }
        const cur = state.current;
        if (data.final_text) cur.textBuf = data.final_text;
        removeCursor(true);
        cur.textEl.innerHTML = injectFileTokens(renderMarkdown(cur.textBuf || ''));
        promoteImageLinks(cur.textEl);
        clearTransientStatus();
        finishTurn();
        break;
      }
      case 'error':
        showErrorBlock(data.message || 'Error');
        setStatus('error');
        finishTurn();
        break;
    }
  }
  function finishTurn() {
    if (state.es) { try { state.es.close(); } catch (_) {} state.es = null; }
    state.streamId = null; state.retried = false; state.current = null;
    setClarifyState(null);
    setStatus('idle');
    // Notify when tab is not focused
    if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
      try { new Notification('Hermes', { body: 'Response ready' }); } catch (_) {}
    }
  }

  // ---------- Sending ----------
  async function sendMessage() {
    if (state.clarify) {
      const answer = input.value.trim();
      if (!answer) return;
      return submitClarifyAnswer(answer);
    }
    const text = input.value.trim();
    if (!text && !state.attachments.length) return;
    if (state.es) return;

    if (text.startsWith('/')) {
      const m = text.match(/^\/(\w+)\s*(.*)$/);
      if (m && ['help','new','clear','model','quit'].includes(m[1].toLowerCase())) {
        input.value = ''; autosizeInput(); hideSlash();
        return runCommand(m[1].toLowerCase(), m[2] || '');
      }
    }

    const attTokens = state.attachments.map((a) => a.token).filter(Boolean);
    const attUrls = state.attachments.map((a) => withTokenQuery(a.url));
    appendUserMessage(text, attUrls);
    input.value = ''; autosizeInput(); clearAttachments(); hideSlash();
    setStatus('thinking');
    try {
      const res = await apiJson('/api/message', { text, attachments: attTokens });
      if (!res || !res.stream_id) throw new Error('no stream_id');
      startAssistantMessage();
      openStream(res.stream_id);
    } catch (err) {
      showErrorBlock('Failed to send: ' + (err && err.message || err));
      setStatus('idle');
    }
  }

  // ---------- Slash commands ----------
  const SLASH_CMDS = [
    { name: 'help',  hint: 'show available commands' },
    { name: 'new',   hint: 'start a new session' },
    { name: 'clear', hint: 'clear the conversation' },
    { name: 'model', hint: 'show current model & provider' },
    { name: 'quit',  hint: 'shut down the server' },
  ];
  let slashIdx = 0;
  function updateSlash() {
    if (state.clarify) return hideSlash();
    const v = input.value;
    if (!v.startsWith('/')) return hideSlash();
    const q = v.slice(1).split(/\s/)[0].toLowerCase();
    const matches = SLASH_CMDS.filter((c) => c.name.startsWith(q));
    if (!matches.length) return hideSlash();
    slashIdx = Math.min(slashIdx, matches.length - 1);
    slashPop.innerHTML = matches.map((c, i) => `
      <div class="slash-item flex items-baseline gap-3 px-3 py-2 cursor-pointer text-[13px] ${i === slashIdx ? 'bg-zinc-100' : 'hover:bg-zinc-50'}" data-name="${c.name}">
        <span class="font-mono text-accent min-w-[60px]">/${escapeHtml(c.name)}</span>
        <span class="text-zinc-500">${escapeHtml(c.hint)}</span>
      </div>`).join('');
    show(slashPop);
    slashPop.querySelectorAll('.slash-item').forEach((el) => {
      el.addEventListener('mousedown', (e) => { e.preventDefault(); completeSlash(el.dataset.name); });
    });
  }
  function hideSlash() { hide(slashPop); slashIdx = 0; }
  function moveSlash(dir) {
    const items = slashPop.querySelectorAll('.slash-item');
    if (!items.length) return;
    slashIdx = (slashIdx + dir + items.length) % items.length;
    items.forEach((el, i) => {
      el.classList.toggle('bg-zinc-100', i === slashIdx);
      el.classList.toggle('hover:bg-zinc-50', i !== slashIdx);
    });
  }
  function completeSlash(name) {
    if (!name) {
      const a = slashPop.querySelector('.slash-item.bg-zinc-100');
      if (!a) return;
      name = a.dataset.name;
    }
    input.value = '/' + name + ' ';
    hideSlash(); input.focus();
  }
  async function runCommand(name, args) {
    appendUserMessage('/' + name + (args ? ' ' + args : ''), []);
    if (name === 'clear') {
      try {
        await apiJson('/api/command', { name, args: args || '' });
      } catch (_) {}
      messagesEl.innerHTML = '';
      refreshSessions().catch(() => {});
      return;
    }
    if (name === 'new') {
      messagesEl.innerHTML = '';
      try {
        const res = await apiJson('/api/command', { name, args: args || '' });
        const note = document.createElement('div');
        note.setAttribute('data-anim', 'msg-in');
        note.className = 'mx-auto text-[12px] text-zinc-500 italic py-2';
        note.textContent = res.text || 'New session started.';
        messagesEl.appendChild(note);
        scrollBottom(true);
        refreshSessions().catch(() => {});
      } catch (err) {
        showErrorBlock('Command failed: ' + (err && err.message || err));
      }
      return;
    }
    try {
      const res = await apiJson('/api/command', { name, args: args || '' });
      const wrap = makeMessage('assistant');
      const t = assistantTextEl();
      t.innerHTML = renderMarkdown(res.text || '');
      wrap._body.appendChild(t);
      messagesEl.appendChild(wrap);
      scrollBottom(true);
      if (res.shutdown) { setStatus('idle'); statusPill.textContent = 'shutting down'; }
    } catch (err) {
      showErrorBlock('Command failed: ' + (err && err.message || err));
    }
  }

  // ---------- Attachments ----------
  function renderAttachments() {
    attachmentsEl.innerHTML = '';
    state.attachments.forEach((a, i) => {
      const chip = document.createElement('div');
      chip.setAttribute('data-anim', 'msg-in');
      chip.className = 'relative w-14 h-14 rounded-lg overflow-hidden border border-zinc-200 bg-zinc-50' + (a.token ? '' : ' opacity-60');
      const img = document.createElement('img');
      img.src = a.localPreview || withTokenQuery(a.url);
      img.className = 'w-full h-full object-cover block';
      chip.appendChild(img);
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'absolute top-0.5 right-0.5 w-4 h-4 rounded-full bg-black/70 text-white text-[11px] leading-none flex items-center justify-center hover:bg-black';
      x.textContent = '×';
      x.addEventListener('click', () => { state.attachments.splice(i, 1); renderAttachments(); });
      chip.appendChild(x);
      attachmentsEl.appendChild(chip);
    });
  }
  const clearAttachments = () => { state.attachments = []; renderAttachments(); };
  async function uploadFile(file) {
    if (!file || !file.type.startsWith('image/')) return;
    const placeholder = { token: null, url: null, filename: file.name, localPreview: URL.createObjectURL(file) };
    state.attachments.push(placeholder); renderAttachments();
    try {
      const fd = new FormData(); fd.append('file', file);
      const res = await fetch('/api/upload', {
        method: 'POST',
        headers: authHeaders(),
        credentials: 'same-origin',
        body: fd,
      });
      if (res.status === 401) { gotUnauthorized(); throw new Error('unauthorized'); }
      if (!res.ok) throw new Error('upload ' + res.status);
      const j = await res.json();
      placeholder.token = j.token; placeholder.url = j.url; placeholder.filename = j.filename || file.name;
      renderAttachments();
    } catch (err) {
      const idx = state.attachments.indexOf(placeholder);
      if (idx >= 0) state.attachments.splice(idx, 1);
      renderAttachments();
      showErrorBlock('Upload failed: ' + (err && err.message || err));
    }
  }

  // ---------- Composer / inputs ----------
  function autosizeInput() {
    input.style.height = 'auto';
    input.style.height = Math.min(160, input.scrollHeight) + 'px';
  }
  input.addEventListener('input', () => { autosizeInput(); updateSlash(); });
  input.addEventListener('keydown', (e) => {
    const slashOpen = !isHidden(slashPop);
    if (slashOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); return moveSlash(1); }
      if (e.key === 'ArrowUp')   { e.preventDefault(); return moveSlash(-1); }
      if (e.key === 'Tab')       { e.preventDefault(); return completeSlash(); }
      if (e.key === 'Escape')    { e.preventDefault(); return hideSlash(); }
      if (e.key === 'Enter' && !e.shiftKey) {
        // If a full command name is already typed, send. Otherwise complete.
        const m = input.value.match(/^\/(\w+)/);
        if (!(m && SLASH_CMDS.some((c) => c.name === m[1].toLowerCase()))) {
          e.preventDefault(); return completeSlash();
        }
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  composer.addEventListener('submit', (e) => { e.preventDefault(); sendMessage(); });
  stopBtn.addEventListener('click', async () => {
    if (!state.streamId) return;
    try { await apiJson(`/api/cancel/${encodeURIComponent(state.streamId)}`, {}); }
    catch (_) { /* best-effort */ }
  });
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    Array.from(fileInput.files || []).forEach(uploadFile);
    fileInput.value = '';
  });
  input.addEventListener('paste', (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.kind === 'file') {
        const f = it.getAsFile();
        if (f && f.type.startsWith('image/')) { e.preventDefault(); uploadFile(f); }
      }
    }
  });

  // Drag-drop image
  let dragDepth = 0;
  window.addEventListener('dragenter', (e) => { if (e.dataTransfer) { dragDepth++; document.body.classList.add('drag-over'); } });
  window.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) document.body.classList.remove('drag-over'); });
  window.addEventListener('dragover',  (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    e.preventDefault(); dragDepth = 0; document.body.classList.remove('drag-over');
    Array.from((e.dataTransfer && e.dataTransfer.files) || []).forEach(uploadFile);
  });

  // ---------- Info popover ----------
  infoBtn.addEventListener('click', async () => {
    if (!isHidden(popover)) { hide(popover); return; }
    popover.textContent = 'loading…';
    show(popover);
    try {
      const res = await apiJson('/api/command', { name: 'model', args: '' });
      popover.textContent = (res && res.text) || '(no info)';
    } catch (err) {
      popover.textContent = 'Error: ' + (err && err.message || err);
    }
  });
  document.addEventListener('click', (e) => {
    if (!isHidden(popover) && !popover.contains(e.target) && !infoBtn.contains(e.target)) hide(popover);
  });

  // ---------- API GET + tiny helpers ----------
  async function apiGet(path) {
    const res = await fetch(path, {
      headers: authHeaders(),
      credentials: 'same-origin',
    });
    if (res.status === 401) { gotUnauthorized(); throw new Error('unauthorized'); }
    if (!res.ok) throw new Error(`${path} ${res.status}`);
    return res.json();
  }
  const toastEl = $('toast');
  let toastTimer = null;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg; show(toastEl);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => hide(toastEl), 1800);
  }
  function relTime(iso) {
    if (!iso) return '';
    const t = Date.parse(iso); if (isNaN(t)) return '';
    const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  // ---------- Sidebar ----------
  const sidebarEl = $('sidebar'), sidebarBackdrop = $('sidebar-backdrop'),
        sidebarToggle = $('sidebar-toggle'), sidebarClose = $('sidebar-close'),
        sessionListEl = $('session-list'), sessionNewBtn = $('session-new');
  const sidebarState = { open: false, items: [] };
  function openSidebar() {
    sidebarState.open = true;
    sidebarEl.setAttribute('data-open', 'true');
    sidebarBackdrop.setAttribute('data-open', 'true');
    refreshSessions().catch(() => {});
  }
  function closeSidebar() {
    sidebarState.open = false;
    sidebarEl.setAttribute('data-open', 'false');
    sidebarBackdrop.setAttribute('data-open', 'false');
  }
  sidebarToggle.addEventListener('click', () => sidebarState.open ? closeSidebar() : openSidebar());
  sidebarClose.addEventListener('click', closeSidebar);
  sidebarBackdrop.addEventListener('click', closeSidebar);

  async function refreshSessions() {
    try {
      const res = await apiGet('/api/sessions');
      sidebarState.items = (res && res.items) || [];
      renderSessions();
    } catch (_) {}
  }
  function renderSessions() {
    sessionListEl.innerHTML = '';
    sidebarState.items.forEach((s) => sessionListEl.appendChild(buildSessionRow(s)));
    if (!sidebarState.items.length) {
      const e = document.createElement('div');
      e.className = 'px-4 py-3 text-[12px] text-zinc-500';
      e.textContent = 'No sessions yet.';
      sessionListEl.appendChild(e);
    }
  }
  function buildSessionRow(s) {
    const row = document.createElement('div');
    row.className = 'session-row group relative px-3 py-2 mx-1 rounded-md cursor-pointer hover:bg-zinc-50';
    row.setAttribute('role', 'listitem');
    row.dataset.id = s.id;
    if (s.is_current) { row.dataset.active = 'true'; row.classList.add('bg-zinc-100'); }
    const title = (s.title && s.title.trim()) || 'Untitled';
    row.innerHTML = `<div class="flex items-center gap-2"><div class="flex-1 min-w-0"><div class="session-title truncate text-[13px] text-zinc-900">${escapeHtml(title)}</div><div class="text-[11px] text-zinc-500">${escapeHtml(relTime(s.updated_at || s.created_at))}</div></div><button type="button" class="session-menu opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center justify-center w-6 h-6 rounded text-zinc-500 hover:text-zinc-900 hover:bg-zinc-200/70" title="More" aria-label="More"><svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg></button></div><div class="session-menu-pop hidden mt-2 rounded-md border border-zinc-200 bg-white shadow-sm overflow-hidden"><button type="button" data-act="rename" class="w-full text-left px-3 py-1.5 text-[12px] text-zinc-700 hover:bg-zinc-50">Rename</button><button type="button" data-act="delete" class="w-full text-left px-3 py-1.5 text-[12px] text-zinc-700 hover:bg-red-50 hover:text-red-700">Delete</button></div><div class="session-confirm hidden mt-2 flex items-center gap-2 text-[12px]"><span class="text-zinc-700">Delete?</span><button type="button" data-act="confirm-yes" class="px-2 py-0.5 rounded bg-red-600 text-white hover:bg-red-700">Yes</button><button type="button" data-act="confirm-no" class="px-2 py-0.5 rounded bg-zinc-100 text-zinc-700 hover:bg-zinc-200">No</button></div>`;
    const titleEl = row.querySelector('.session-title');
    const menuBtn = row.querySelector('.session-menu');
    const menuPop = row.querySelector('.session-menu-pop');
    const confirm = row.querySelector('.session-confirm');
    row.addEventListener('click', async (e) => {
      if (e.target.closest('.session-menu, .session-menu-pop, .session-confirm, input')) return;
      await switchSession(s.id, title);
    });
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      sessionListEl.querySelectorAll('.session-menu-pop').forEach((p) => { if (p !== menuPop) p.classList.add('hidden'); });
      menuPop.classList.toggle('hidden');
    });
    menuPop.querySelector('[data-act="rename"]').addEventListener('click', (e) => {
      e.stopPropagation(); menuPop.classList.add('hidden'); startRename(s, titleEl);
    });
    menuPop.querySelector('[data-act="delete"]').addEventListener('click', (e) => {
      e.stopPropagation(); menuPop.classList.add('hidden'); confirm.classList.remove('hidden');
    });
    confirm.querySelector('[data-act="confirm-yes"]').addEventListener('click', (e) => { e.stopPropagation(); deleteSession(s.id); });
    confirm.querySelector('[data-act="confirm-no"]').addEventListener('click', (e) => { e.stopPropagation(); confirm.classList.add('hidden'); });
    return row;
  }

  function startRename(s, titleEl) {
    const oldTitle = titleEl.textContent;
    const inputEl = document.createElement('input');
    inputEl.type = 'text';
    inputEl.value = oldTitle === 'Untitled' ? '' : oldTitle;
    inputEl.className = 'w-full text-[13px] px-1.5 py-0.5 rounded border border-zinc-300 bg-white outline-none focus:border-accent';
    titleEl.replaceWith(inputEl); inputEl.focus(); inputEl.select();
    const cancel = () => { if (inputEl.parentNode) inputEl.replaceWith(titleEl); };
    const commit = async () => {
      const v = inputEl.value.trim();
      if (!v || v === oldTitle) return cancel();
      try { await apiJson('/api/sessions/rename', { id: s.id, title: v }); await refreshSessions(); }
      catch (_) { cancel(); }
    };
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    inputEl.addEventListener('blur', commit);
    inputEl.addEventListener('click', (e) => e.stopPropagation());
  }

  async function switchSession(id, title) {
    try {
      const res = await apiJson('/api/sessions/switch', { id });
      messagesEl.innerHTML = '';
      const msgs = (res && Array.isArray(res.messages)) ? res.messages : [];
      replayHistory(msgs);
      if (!msgs.length) {
        const note = document.createElement('div');
        note.setAttribute('data-anim', 'msg-in');
        note.className = 'mx-auto text-[12px] text-zinc-500 italic py-2';
        note.textContent = 'switched to ' + (title || 'session') + ' — no prior messages';
        messagesEl.appendChild(note);
      }
      await refreshSessions(); closeSidebar();
      scrollBottom(true);
    } catch (_) { toast('Switch failed'); }
  }

  function replayHistory(messages) {
    for (const m of messages) {
      if (m.role === 'user') {
        appendUserMessage(m.text || '', null);
        continue;
      }
      // assistant: build a static message — no streaming animations, no cursor.
      const wrap = makeMessage('assistant');
      const textEl = assistantTextEl();
      const html = injectFileTokens(renderMarkdown(m.text || ''));
      textEl.innerHTML = html;
      promoteImageLinks(textEl);
      wrap._body.appendChild(textEl);
      messagesEl.appendChild(wrap);
    }
  }
  async function deleteSession(id) {
    try {
      const res = await apiJson('/api/sessions/delete', { id });
      if (res && res.switched) {
        messagesEl.innerHTML = '';
        const msgs = Array.isArray(res.messages) ? res.messages : [];
        replayHistory(msgs);
        if (!msgs.length) {
          const note = document.createElement('div');
          note.setAttribute('data-anim', 'msg-in');
          note.className = 'mx-auto text-[12px] text-zinc-500 italic py-2';
          if (res.created_new) {
            note.textContent = 'started a new session';
          } else {
            const cur = res.current || {};
            const title = (cur.title && cur.title.trim()) || 'session';
            note.textContent = 'switched to ' + title + ' — no prior messages';
          }
          messagesEl.appendChild(note);
        }
        scrollBottom(true);
      }
      await refreshSessions();
    } catch (_) { toast('Delete failed'); }
  }
  async function createSession() {
    try { await apiJson('/api/sessions/new', {}); messagesEl.innerHTML = ''; await refreshSessions(); }
    catch (_) { toast('Create failed'); }
  }
  sessionNewBtn.addEventListener('click', createSession);
  document.addEventListener('click', (e) => {
    if (!sidebarEl.contains(e.target))
      sessionListEl.querySelectorAll('.session-menu-pop').forEach((p) => p.classList.add('hidden'));
  });

  // ---------- Model picker ----------
  const modelBadge = $('model-badge'), modelBadgeLabel = $('model-badge-label'),
        modelModal = $('model-modal'), modelModalClose = $('model-modal-close'),
        modelModalBackdrop = $('model-modal-backdrop'),
        providerListEl = $('provider-list'), modelListEl = $('model-list'),
        modelCurrentLine = $('model-current-line'), modelStatusEl = $('model-modal-status');
  const modelState = { providers: [], current: null, selectedProvider: null, modelsCache: {} };

  function setBadgeLabel(label) {
    const s = (label || 'model').toString();
    modelBadgeLabel.textContent = s.length > 24 ? s.slice(0, 23) + '…' : s;
  }
  async function refreshCurrentModel() {
    try {
      const res = await apiGet('/api/model/current');
      modelState.current = res || null;
      setBadgeLabel((res && (res.label || res.model)) || 'model');
      if (modelCurrentLine) modelCurrentLine.textContent = res ? `${res.provider} · ${res.model}` : '';
    } catch (_) {}
  }
  const setModelStatus = (m) => { if (modelStatusEl) modelStatusEl.textContent = m || ''; };
  function openModelModal() {
    modelModal.setAttribute('data-open', 'true'); setModelStatus('');
    loadProviders().catch(() => setModelStatus('Failed to load providers'));
  }
  const closeModelModal = () => modelModal.setAttribute('data-open', 'false');
  modelBadge.addEventListener('click', openModelModal);
  modelModalClose.addEventListener('click', closeModelModal);
  modelModalBackdrop.addEventListener('click', closeModelModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modelModal.getAttribute('data-open') === 'true') closeModelModal();
  });

  async function loadProviders() {
    const res = await apiGet('/api/providers');
    modelState.providers = (res && res.items) || [];
    let sel = (modelState.current && modelState.current.provider) || null;
    if (!sel) { const cur = modelState.providers.find((p) => p.is_current); sel = cur ? cur.slug : (modelState.providers[0] && modelState.providers[0].slug); }
    modelState.selectedProvider = sel;
    renderProviders();
    if (sel) await loadModelsFor(sel);
  }
  function renderProviders() {
    providerListEl.innerHTML = '';
    modelState.providers.forEach((p) => {
      const isSel = p.slug === modelState.selectedProvider;
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'w-full text-left px-3 py-2 text-[13px] flex items-center gap-2 ' + (isSel ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-700 hover:bg-zinc-50');
      row.innerHTML = `<span class="flex-1 truncate">${escapeHtml(p.name || p.slug)}</span>${p.is_current ? '<span class="text-[10px] uppercase tracking-wider text-accent">current</span>' : ''}`;
      row.addEventListener('click', async () => { modelState.selectedProvider = p.slug; renderProviders(); await loadModelsFor(p.slug); });
      providerListEl.appendChild(row);
    });
  }
  async function loadModelsFor(slug) {
    if (modelState.modelsCache[slug]) return renderModels(slug, modelState.modelsCache[slug]);
    modelListEl.innerHTML = '<div class="px-4 py-3 text-[12px] text-zinc-500">loading…</div>';
    try {
      const res = await apiGet('/api/models?slug=' + encodeURIComponent(slug));
      const list = (res && res.models) || [];
      modelState.modelsCache[slug] = list; renderModels(slug, list);
    } catch (_) { modelListEl.innerHTML = '<div class="px-4 py-3 text-[12px] text-red-600">failed to load models</div>'; }
  }
  function renderModels(slug, models) {
    modelListEl.innerHTML = '';
    if (!models.length) { modelListEl.innerHTML = '<div class="px-4 py-3 text-[12px] text-zinc-500">No models.</div>'; return; }
    const curModel = modelState.current && modelState.current.provider === slug ? modelState.current.model : null;
    models.forEach((m) => {
      const isCur = m === curModel;
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'w-full text-left px-3 py-2 text-[13px] font-mono flex items-center gap-2 ' + (isCur ? 'bg-accent/10 text-accent-600' : 'text-zinc-800 hover:bg-zinc-50');
      row.innerHTML = `<span class="flex-1 truncate">${escapeHtml(m)}</span>${isCur ? '<span class="text-[10px] uppercase tracking-wider">current</span>' : ''}`;
      row.addEventListener('click', () => switchModel(slug, m));
      modelListEl.appendChild(row);
    });
  }
  async function switchModel(slug, model) {
    setModelStatus('switching…');
    try {
      const res = await apiJson('/api/model/switch', { provider: slug, model });
      if (!res || !res.ok) throw new Error('switch failed');
      await refreshCurrentModel();
      toast('switched to ' + model);
      closeModelModal();
    } catch (err) { setModelStatus('Switch failed: ' + (err && err.message || err)); }
  }

  // Wrap finishTurn so we refresh sessions list once per turn (titles may change server-side).
  const _finishTurn = finishTurn;
  finishTurn = function () {
    _finishTurn();
    if (sidebarState.open || sidebarState.items.length) refreshSessions().catch(() => {});
  };

  // ---------- Init ----------
  setStatus('idle'); autosizeInput(); input.focus();
  // Request notification permission (browser will ask once)
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
  refreshCurrentModel().catch(() => {});
  refreshSessions().then(() => {
    const latestSession = sidebarState.items && sidebarState.items[0];
    if (!latestSession) return;
    // The server now resumes the latest persisted session at startup.
    // Replay it into the UI when it has visible history, but skip the
    // synthetic empty-current case on first-ever boot.
    if (latestSession.is_current && !latestSession.message_count) return;
    switchSession(latestSession.id, latestSession.title || 'Untitled');
  }).catch(() => {});
  window.__hermes = { state, sidebarState, modelState };
})();
