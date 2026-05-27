#!/usr/bin/env bash
# Circuit-Agent uninstaller for Linux and macOS.
#   - Stops any running circuit-proxy spawned by the agent
#   - Removes ~/.local/bin/circuit-{agent,proxy} symlinks
#   - Deletes the config dir ($CIRCUIT_AGENT_HOME or ~/.circuit-agent),
#     including the venv, .env credentials, proxy.log, and token cache
#
# Usage:
#   ./uninstall.sh        # interactive, asks for confirmation
#   ./uninstall.sh -y     # non-interactive

set -euo pipefail

CONFIG_DIR="${CIRCUIT_AGENT_HOME:-$HOME/.circuit-agent}"
VENV_DIR="$CONFIG_DIR/venv"
BIN_DIR="$HOME/.local/bin"

ASSUME_YES=0
if [ "${1:-}" = "-y" ] || [ "${1:-}" = "--yes" ]; then
    ASSUME_YES=1
fi

echo "==> Circuit-Agent uninstaller"
echo "    config:  $CONFIG_DIR"
echo "    bins:    $BIN_DIR/circuit-{agent,proxy}"
echo

if [ ! -d "$CONFIG_DIR" ] && [ ! -L "$BIN_DIR/circuit-agent" ] && [ ! -L "$BIN_DIR/circuit-proxy" ]; then
    echo "Nothing to remove — Circuit-CLI doesn't appear to be installed."
    exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    echo "This will delete:"
    [ -d "$CONFIG_DIR" ]            && echo "  - $CONFIG_DIR (venv, .env credentials, proxy.log)"
    [ -L "$BIN_DIR/circuit-agent" ] && echo "  - $BIN_DIR/circuit-agent (symlink)"
    [ -L "$BIN_DIR/circuit-proxy" ] && echo "  - $BIN_DIR/circuit-proxy (symlink)"
    echo
    read -rp "Continue? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES) ;;
        *) echo "Aborted."; exit 0 ;;
    esac
fi

# --- stop running proxy ----------------------------------------------------
if [ -x "$VENV_DIR/bin/python" ]; then
    PROXY_PIDS=$(pgrep -f "$VENV_DIR/bin/python.*circuit_agent.proxy" 2>/dev/null || true)
    if [ -n "$PROXY_PIDS" ]; then
        echo "==> Stopping running circuit-proxy ($PROXY_PIDS)"
        # shellcheck disable=SC2086
        kill $PROXY_PIDS 2>/dev/null || true
        sleep 0.5
        # shellcheck disable=SC2086
        kill -9 $PROXY_PIDS 2>/dev/null || true
    fi
fi

# --- remove symlinks -------------------------------------------------------
for link in "$BIN_DIR/circuit-agent" "$BIN_DIR/circuit-proxy"; do
    if [ -L "$link" ] || [ -e "$link" ]; then
        echo "==> Removing $link"
        rm -f "$link"
    fi
done

# --- remove config dir -----------------------------------------------------
if [ -d "$CONFIG_DIR" ]; then
    echo "==> Removing $CONFIG_DIR"
    rm -rf "$CONFIG_DIR"
fi

echo
echo "==> Uninstall complete."
echo
echo "The cloned repo at $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) was NOT touched."
echo "Delete it manually if you don't need it anymore."
