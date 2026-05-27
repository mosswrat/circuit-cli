#!/usr/bin/env bash
# Circuit-CLI one-liner installer for Linux and macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mosswrat/circuit-cli/main/install/web-install.sh | bash
#
# Installs circuit-agent into ~/.circuit-agent/venv, symlinks into ~/.local/bin,
# and prints next steps. Credentials are NOT prompted here — they're collected
# the first time you run `circuit-agent`.

set -euo pipefail

REPO_URL="https://github.com/mosswrat/circuit-cli.git"
CONFIG_DIR="${CIRCUIT_AGENT_HOME:-$HOME/.circuit-agent}"
VENV_DIR="$CONFIG_DIR/venv"
BIN_DIR="$HOME/.local/bin"

OS_RAW=$(uname -s)
case "$OS_RAW" in
    Linux*)  OS_NAME="Linux"  ;;
    Darwin*) OS_NAME="macOS"  ;;
    *) echo "Unsupported OS: $OS_RAW"; exit 1 ;;
esac

echo "==> Circuit-CLI installer ($OS_NAME)"

# --- Python ----------------------------------------------------------------
PY=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PY="$cmd"; break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.10+ not found. Install from https://www.python.org/downloads/"
    exit 1
fi
echo "    python: $PY ($("$PY" --version 2>&1))"

# --- venv + pip install from git ------------------------------------------
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "==> Creating venv at $VENV_DIR"
    "$PY" -m venv "$VENV_DIR"
fi

echo "==> Installing circuit-agent from $REPO_URL"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet --upgrade "git+$REPO_URL"

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
    echo "    Then open a new shell and run:"
else
    echo "    Run:"
fi
echo "        circuit-agent"
echo
echo "    On first run you'll be prompted for your Cisco CIRCUIT credentials."
