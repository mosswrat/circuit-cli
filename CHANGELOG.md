# Changelog

All notable changes to Circuit-CLI are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Dates are UTC.

---

## [5.2.0] — 2026-05-28

### Added
- `CIRCUIT_REPO_URL` env var lets you `--upgrade` from a fork or private mirror.
- `circuit-proxy` now exposes `/shutdown` (POST). `circuit-agent --upgrade` calls
  it so the next session boots a fresh proxy carrying the upgraded code, instead
  of an old proxy lingering with stale logic.
- Tests for the dynamic `__version__` resolution path, the proxy-restart call,
  and the new atomic credential writer.

### Changed
- `write_env_file` is now **atomic** (tempfile → `os.replace`) and creates the
  file with `0o600` from the instant it exists — closing the brief
  world-readable window between an old `write_text` + `os.chmod`. Also no more
  half-written `.env` files if the agent dies mid-save.
- Spawn-marker staleness threshold tightened from 30s → **10s** so a crashed
  spawner is recovered faster (proxy boot is ~1–2s).
- `_upgrade_in_place` now warns and returns exit code `2` if the new binary
  fails to report its version (typically a missing dep or broken import),
  instead of silently claiming success.

---

## [5.1.1] — 2026-05-28

### Fixed
- `--version` was stuck printing `5.0.0-alpha` after every upgrade because
  `__version__` was hardcoded in `circuit_agent/__init__.py` while
  `pyproject.toml` was bumped independently. Now reads dynamically from
  `importlib.metadata`, so `pyproject.toml` is the single source of truth.

---

## [5.1.0] — 2026-05-28

### Added
- `circuit-agent --upgrade` (alias `--update`) pulls the latest build from
  GitHub. Bakes in `--no-cache-dir --force-reinstall` so pip's `git+` URL
  cache can't silently no-op the upgrade.
- Spawn marker (`proxy.spawning`) prevents two agents launched in the same
  ~100 ms window from both calling Popen and racing on port 8787.
- Uninstaller scripts: `install/uninstall.sh` and `install/uninstall.ps1`.
- `tests/test_cli.py` — 20 tests covering the new helpers, no real subprocess
  or HTTP calls.

### Changed
- Credential prompt now lives **inside** the TUI, after the welcome screen.
  Secret + app key are masked with asterisks via `prompt_toolkit.PromptSession`.
- Proxy auto-spawn is **fire-and-forget** — no 8-second wait after auth. The
  agent talks to Cisco directly; if the proxy fails to bind, the user finds out
  from `proxy.log`.
- `web-install.sh` auto-appends `PATH` to `~/.zshrc` (zsh users) or `~/.bashrc`
  (bash users) — no more stray empty rc files for the wrong shell.
- `.env` is now the canonical credential store. The old keyring / config-file
  save prompt is removed.

### Fixed
- `getpass()` failed in async context with `RuntimeError: asyncio.run() cannot
  be called from a running event loop`. Replaced with
  `PromptSession.prompt_async(is_password=True)`.
- `py.exe` without a registered Python no longer breaks the Windows installer.
- `circuit-agent` not on PATH on Windows — installer now appends to user PATH.
- App Key is now masked alongside Client Secret in the prompt.

---

## [5.0.0-alpha] — 2026-05-27

Initial standalone release. Extracted from the Circuit-IDE monorepo as a
focused terminal CLI + OpenAI-compatible proxy bundle.

- `circuit-agent`: terminal coding agent for Cisco CIRCUIT.
- `circuit-proxy`: OpenAI-compatible HTTP proxy on localhost:8787.
- One-command install: `curl ... | bash` (Unix) or `irm ... | iex` (Windows).
