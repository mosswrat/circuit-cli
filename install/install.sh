#!/usr/bin/env bash
# Circuit-Agent installer for Linux and macOS.
#   - Creates a venv at $CIRCUIT_AGENT_HOME (default: ~/.circuit-agent/venv)
#   - Installs the circuit-agent package (includes circuit-proxy)
#   - Prompts for Cisco CIRCUIT credentials and writes ~/.circuit-agent/.env (0600)
#   - Symlinks circuit-agent and circuit-proxy into ~/.local/bin

set -euo pipefail

OS_RAW=$(uname -s)
case "$OS_RAW" in
    Linux*)  OS_NAME="Linux"  ;;
    Darwin*) OS_NAME="macOS"  ;;
    *) echo "Unsupported OS: $OS_RAW (use install.ps1 on Windows)"; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="${CIRCUIT_AGENT_HOME:-$HOME/.circuit-agent}"
VENV_DIR="$CONFIG_DIR/venv"
ENV_FILE="$CONFIG_DIR/.env"
BIN_DIR="$HOME/.local/bin"

echo "==> Circuit-Agent installer ($OS_NAME)"
echo "    repo:    $REPO_DIR"
echo "    config:  $CONFIG_DIR"

# --- Python ----------------------------------------------------------------
PY=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then PY="$cmd"; break; fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.10+ not found. Install from https://www.python.org/downloads/"
    exit 1
fi
PY_OK=$("$PY" -c "import sys; print(int(sys.version_info >= (3,10)))")
if [ "$PY_OK" != "1" ]; then
    PY_VER=$("$PY" -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")
    echo "ERROR: Python 3.10+ required, found $PY_VER ($PY)"
    exit 1
fi
echo "    python:  $PY ($("$PY" --version 2>&1))"

# --- venv + install --------------------------------------------------------
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "==> Creating venv at $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
fi

echo "==> Installing circuit-agent into venv"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_DIR"

# --- credentials prompt ----------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    echo "==> Existing credentials found at $ENV_FILE — keeping as-is."
    echo "    (Delete the file and re-run if you want to enter new values.)"
else
    echo
    echo "==> Enter your Cisco CIRCUIT credentials"
    echo "    Get them from your Cisco AI portal. They are stored locally only."
    echo
    read -rp  "    API Key (CIRCUIT_CLIENT_ID): " CID
    read -rsp "    Secret  (CIRCUIT_CLIENT_SECRET): " CSEC; echo
    read -rsp "    KeyPass (CIRCUIT_APP_KEY): " CAPP;     echo

    if [ -z "$CID" ] || [ -z "$CSEC" ] || [ -z "$CAPP" ]; then
        echo "ERROR: all three values are required."
        exit 1
    fi

    umask 077
    cat > "$ENV_FILE" <<EOF
# Cisco CIRCUIT API credentials — keep this file private (chmod 600)
CIRCUIT_CLIENT_ID=$CID
CIRCUIT_CLIENT_SECRET=$CSEC
CIRCUIT_APP_KEY=$CAPP
CIRCUIT_MODEL=gpt-5-nano
EOF
    chmod 600 "$ENV_FILE"
    echo "    Wrote $ENV_FILE (0600)"
fi

# --- PATH symlinks ---------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/circuit-agent" "$BIN_DIR/circuit-agent"
ln -sf "$VENV_DIR/bin/circuit-proxy" "$BIN_DIR/circuit-proxy"

# --- done ------------------------------------------------------------------
echo
echo "==> Installation complete."
echo
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
    echo "    NOTE: $BIN_DIR is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
    echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo
fi
echo "    Start the proxy (one terminal):  circuit-proxy"
echo "    Run the agent  (another):        circuit-agent"
