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

After cloning this folder onto the target machine, run the command for your OS from inside it.

### Linux / macOS

```bash
./install/install.sh
```

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

The installer will:

1. Find a Python ≥ 3.10 on the system (errors out with a download link if not present).
2. Create an isolated venv at `~/.circuit-agent/venv` (or `%USERPROFILE%\.circuit-agent\venv` on Windows).
3. `pip install` the `circuit-agent` package and its dependencies.
4. Prompt you for the three Cisco CIRCUIT credentials and save them to `~/.circuit-agent/.env`:

```
==> Enter your Cisco CIRCUIT credentials
    API Key (CIRCUIT_CLIENT_ID): _
    Secret  (CIRCUIT_CLIENT_SECRET): _   (hidden input)
    KeyPass (CIRCUIT_APP_KEY): _         (hidden input)
```

The file is created with `0600` permissions on Unix and an ACL-restricted to the current user on Windows.

5. On Linux/macOS, symlink `circuit-agent` and `circuit-proxy` into `~/.local/bin`. (The installer warns if that's not on your `PATH`.)

---

## Run

In two terminals:

```bash
# terminal 1
circuit-proxy

# terminal 2
circuit-agent
```

That's it.

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

## Where credentials come from

Get them from your Cisco AI portal (`https://developer.cisco.com/site/ai-ml/` → "Manage Circuit API Keys"). You need three values:

- **CIRCUIT_CLIENT_ID** — OAuth2 client ID (the installer calls this "API Key")
- **CIRCUIT_CLIENT_SECRET** — OAuth2 client secret ("Secret")
- **CIRCUIT_APP_KEY** — Cisco appkey injected per request ("KeyPass")

---

## Uninstall

```bash
# Linux / macOS
rm -rf ~/.circuit-agent ~/.local/bin/circuit-agent ~/.local/bin/circuit-proxy

# Windows (PowerShell)
Remove-Item -Recurse -Force $env:USERPROFILE\.circuit-agent
```
