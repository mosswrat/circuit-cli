#!/usr/bin/env python3
"""OpenAI-compatible HTTP proxy → Cisco CIRCUIT chat API.

Run as `circuit-proxy` after `pip install circuit-agent`. Any OpenAI SDK (Aider,
Hermes, the bundled `circuit-agent` CLI, etc.) can point at http://127.0.0.1:8787/v1.

Credentials are read from $CIRCUIT_AGENT_HOME/.env if set, else ~/.circuit-agent/.env.
Required keys: CIRCUIT_CLIENT_ID, CIRCUIT_CLIENT_SECRET, CIRCUIT_APP_KEY.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request

TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
CHAT_URL = "https://chat-ai.cisco.com/openai/deployments/{model}/chat/completions?api-version=2025-04-01-preview"
PROXY_PORT = int(os.environ.get("CIRCUIT_PROXY_PORT", "8787"))
PROXY_HOST = os.environ.get("CIRCUIT_PROXY_HOST", "127.0.0.1")


def _config_dir() -> Path:
    override = os.environ.get("CIRCUIT_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".circuit-agent"


CONFIG_DIR = _config_dir()
ENV_FILE = CONFIG_DIR / ".env"
TOKEN_CACHE = CONFIG_DIR / ".token-cache.json"


def _load_env() -> None:
    if not ENV_FILE.exists():
        sys.stderr.write(
            f"circuit-proxy: no credentials file at {ENV_FILE}\n"
            "  Run the install script, or create the file manually with:\n"
            "    CIRCUIT_CLIENT_ID=...\n"
            "    CIRCUIT_CLIENT_SECRET=...\n"
            "    CIRCUIT_APP_KEY=...\n"
        )
        sys.exit(1)
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
    for required in ("CIRCUIT_CLIENT_ID", "CIRCUIT_CLIENT_SECRET", "CIRCUIT_APP_KEY"):
        if not os.environ.get(required):
            sys.stderr.write(f"circuit-proxy: missing {required} in {ENV_FILE}\n")
            sys.exit(1)


app = Flask(__name__)


def get_token() -> str:
    """Mint or reuse a Cisco access token. Refreshes 60s before expiry."""
    if TOKEN_CACHE.exists():
        try:
            cached = json.loads(TOKEN_CACHE.read_text())
            if cached["expires_at"] > time.time() + 60:
                return cached["token"]
        except (json.JSONDecodeError, KeyError):
            pass
    r = requests.post(
        TOKEN_URL,
        auth=(os.environ["CIRCUIT_CLIENT_ID"], os.environ["CIRCUIT_CLIENT_SECRET"]),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "*/*"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(
        json.dumps({"token": data["access_token"], "expires_at": time.time() + data["expires_in"]})
    )
    try:
        TOKEN_CACHE.chmod(0o600)
    except OSError:
        pass  # Windows: chmod is a no-op for these bits
    return data["access_token"]


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model", "gpt-5-nano")
    if "/" in model:
        model = model.split("/", 1)[1]
    body["user"] = json.dumps({"appkey": os.environ["CIRCUIT_APP_KEY"]})
    stream = bool(body.get("stream", False))

    upstream = requests.post(
        CHAT_URL.format(model=model),
        headers={"api-key": get_token(), "Content-Type": "application/json"},
        json=body,
        stream=stream,
        timeout=600,
    )

    if stream:
        def generate():
            for chunk in upstream.iter_lines(decode_unicode=False):
                if chunk:
                    yield chunk + b"\n\n"
        return Response(
            generate(),
            status=upstream.status_code,
            mimetype="text/event-stream",
        )

    return Response(
        upstream.content,
        status=upstream.status_code,
        mimetype=upstream.headers.get("content-type", "application/json"),
    )


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": "gpt-5-nano", "object": "model", "owned_by": "cisco-circuit"},
            {"id": "gemini-3.1-flash-lite", "object": "model", "owned_by": "cisco-circuit"},
        ],
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


def main() -> None:
    _load_env()
    sys.stderr.write(
        f"circuit-proxy: serving Cisco CIRCUIT on http://{PROXY_HOST}:{PROXY_PORT}/v1 "
        f"(credentials: {ENV_FILE})\n"
    )
    app.run(host=PROXY_HOST, port=PROXY_PORT, threaded=True)


if __name__ == "__main__":
    main()
