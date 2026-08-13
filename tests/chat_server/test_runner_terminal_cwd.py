import os


def test_bridge_terminal_cwd_placeholder_sets_home_and_updates_cached_env(monkeypatch, tmp_path):
    from hermes_cli.chat_server.runner import _bridge_terminal_cwd
    from tools import terminal_tool as tt

    old_overrides = dict(tt._task_env_overrides)
    old_envs = dict(tt._active_environments)
    try:
        tt._task_env_overrides.clear()
        tt._active_environments.clear()

        class DummyEnv:
            cwd = "/repo"

        tt._active_environments["default"] = DummyEnv()
        monkeypatch.setenv("TERMINAL_CWD", "/repo")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        cwd = _bridge_terminal_cwd({"terminal": {"cwd": "."}})

        assert cwd == str(tmp_path)
        assert os.environ["TERMINAL_CWD"] == str(tmp_path)
        assert tt._task_env_overrides["default"] == {"cwd": str(tmp_path)}
        assert tt._active_environments["default"].cwd == str(tmp_path)
    finally:
        tt._task_env_overrides.clear()
        tt._task_env_overrides.update(old_overrides)
        tt._active_environments.clear()
        tt._active_environments.update(old_envs)


def test_bridge_terminal_cwd_expands_explicit_path(monkeypatch, tmp_path):
    from hermes_cli.chat_server.runner import _bridge_terminal_cwd
    from tools import terminal_tool as tt

    old_overrides = dict(tt._task_env_overrides)
    try:
        tt._task_env_overrides.clear()
        monkeypatch.setenv("HOME", str(tmp_path))

        cwd = _bridge_terminal_cwd({"terminal": {"cwd": "~/project"}})

        assert cwd == str(tmp_path / "project")
        assert os.environ["TERMINAL_CWD"] == str(tmp_path / "project")
        assert tt._task_env_overrides["default"] == {"cwd": str(tmp_path / "project")}
    finally:
        tt._task_env_overrides.clear()
        tt._task_env_overrides.update(old_overrides)
