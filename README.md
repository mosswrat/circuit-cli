# Circuit-CLI

Terminal coding agent + OpenAI-compatible proxy for the Cisco CIRCUIT API.

Two console scripts ship in this bundle:

| Command | What it does |
|---|---|
| `circuit-proxy` | Runs an OpenAI-compatible HTTP proxy on `127.0.0.1:8787` that translates `/v1/chat/completions` calls into Cisco CIRCUIT requests (OAuth2 token mint + `appkey` injection). |
| `circuit-agent` | Claude-Code-style terminal agent — reads files, runs commands, edits code, with a slash-command palette and tool-call UI. Talks to the proxy. |

Each user supplies their **own** Cisco credentials. Nothing is shared across machines.

---

## Install

One command. No clone required.

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/mosswrat/circuit-cli/main/install/web-install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/mosswrat/circuit-cli/main/install/web-install.ps1 | iex
```

The installer will:

1. Find a Python ≥ 3.10 on the system (errors out with a download link if not present).
2. Create an isolated venv at `~/.circuit-agent/venv` (or `%USERPROFILE%\.circuit-agent\venv` on Windows).
3. `pip install` the package directly from this GitHub repo.
4. Make `circuit-agent` available on PATH — symlinks into `~/.local/bin` on Unix, appends to user PATH on Windows.

Credentials are **not** prompted during install. The first time you run `circuit-agent`, it will ask:

```
==> First run — enter your Cisco CIRCUIT credentials
    API Key (CIRCUIT_CLIENT_ID): _
    Secret  (CIRCUIT_CLIENT_SECRET): _   (hidden input)
    KeyPass (CIRCUIT_APP_KEY): _         (hidden input)
```

The file is created with `0600` permissions on Unix and lives at `~/.circuit-agent/.env`.

### Alternative: install from a local clone

If you'd rather inspect the code before installing, clone the repo and run the bundled script:

```bash
git clone https://github.com/mosswrat/circuit-cli && cd circuit-cli
./install/install.sh                                       # Linux / macOS
# or:
powershell -ExecutionPolicy Bypass -File .\install\install.ps1   # Windows
```

---

## Run

Just one command:

```bash
circuit-agent
```

The agent auto-spawns `circuit-proxy` in the background on first launch (logs go to `~/.circuit-agent/proxy.log`). Subsequent runs reuse the same proxy. To disable this and manage the proxy yourself, export `CIRCUIT_AGENT_AUTO_PROXY=0` before running the agent, then start `circuit-proxy` in a separate terminal.

---

## Where things live

| Path | Purpose |
|---|---|
| `~/.circuit-agent/.env`              | Your credentials (0600). Delete to re-enter via re-running the installer. |
| `~/.circuit-agent/venv/`             | The isolated Python venv. Delete to fully uninstall. |
| `~/.circuit-agent/.token-cache.json` | Cached OAuth2 token (refreshed automatically 60s before expiry). |
| `~/.local/bin/circuit-{agent,proxy}` | PATH symlinks (Linux/macOS only). |

Override the config location by exporting `CIRCUIT_AGENT_HOME=/some/path` before running either command.

---

## Upgrade

```bash
circuit-agent --upgrade
```

That's it. The agent finds its own venv, runs `pip install --upgrade --force-reinstall --no-cache-dir` for the GitHub URL, prints the new version, and exits. No need to remember pip flags. (`--force-reinstall --no-cache-dir` matter because pip otherwise caches `git+...` URLs aggressively and silently no-ops your upgrade.)

---

## Where credentials come from

Get them from your Cisco AI portal (`https://developer.cisco.com/site/ai-ml/` → "Manage Circuit API Keys"). You need three values:

- **CIRCUIT_CLIENT_ID** — OAuth2 client ID (the installer calls this "API Key")
- **CIRCUIT_CLIENT_SECRET** — OAuth2 client secret ("Secret")
- **CIRCUIT_APP_KEY** — Cisco appkey injected per request ("KeyPass")

---

## Uninstall

Run the uninstaller for your OS from inside the cloned repo. It stops any running proxy, removes the PATH entry / symlinks, and deletes the config directory (venv, `.env`, `proxy.log`, token cache).

```bash
# Linux / macOS
./install/uninstall.sh             # asks for confirmation
./install/uninstall.sh -y          # skip the confirmation prompt
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\install\uninstall.ps1
powershell -ExecutionPolicy Bypass -File .\install\uninstall.ps1 -Yes   # non-interactive
```

The cloned repo itself is not touched — delete it manually if you don't need it anymore.
