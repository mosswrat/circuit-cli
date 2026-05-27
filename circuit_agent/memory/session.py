"""
Session persistence for Circuit Agent.

Allows saving and loading conversation sessions.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class SessionManager:
    """Manages saving and loading of conversation sessions."""

    def __init__(self, sessions_dir: Optional[str] = None):
        """
        Initialize session manager.

        Args:
            sessions_dir: Directory to store sessions. Defaults to ~/.config/circuit-agent/sessions/
        """
        if sessions_dir:
            self.sessions_dir = Path(sessions_dir)
        else:
            self.sessions_dir = Path.home() / ".config" / "circuit-agent" / "sessions"

        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_name(self, name: str) -> str:
        """Sanitize session name for use as filename."""
        # Remove or replace invalid characters
        sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return sanitized[:50] or "unnamed"

    def _get_session_path(self, name: str) -> Path:
        """Get the path for a session file."""
        return self.sessions_dir / f"{self._sanitize_name(name)}.json"

    def save(
        self,
        name: str,
        history: List[Dict[str, Any]],
        model: str,
        working_dir: str,
        auto_approve: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Save a session to disk.

        Args:
            name: Session name
            history: Conversation history
            model: Current model name
            working_dir: Working directory path
            auto_approve: Auto-approve mode state
            metadata: Additional metadata to save

        Returns:
            (success, message) tuple
        """
        try:
            session_data = {
                "name": name,
                "created_at": datetime.now().isoformat(),
                "model": model,
                "working_dir": working_dir,
                "auto_approve": auto_approve,
                "history": history,
                "metadata": metadata or {},
                "version": "3.0",
            }

            path = self._get_session_path(name)

            # Create file with restrictive permissions from the start (avoids TOCTOU)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(session_data, f, indent=2, default=str)
            except Exception:
                os.close(fd)
                raise

            return True, f"Session saved: {path}"
        except Exception as e:
            return False, f"Failed to save session: {e}"

    def load(self, name: str) -> tuple[bool, Dict[str, Any] | str]:
        """
        Load a session from disk.

        Args:
            name: Session name

        Returns:
            (success, session_data or error_message) tuple
        """
        path = self._get_session_path(name)

        if not path.exists():
            # Try to find partial match
            matches = list(self.sessions_dir.glob(f"*{self._sanitize_name(name)}*.json"))
            if matches:
                suggestions = [m.stem for m in matches[:5]]
                return False, f"Session not found: {name}\nDid you mean: {', '.join(suggestions)}?"
            return False, f"Session not found: {name}"

        try:
            with open(path, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            return True, session_data
        except json.JSONDecodeError as e:
            return False, f"Invalid session file: {e}"
        except Exception as e:
            return False, f"Failed to load session: {e}"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """
        List all saved sessions.

        Returns:
            List of session info dicts with name, created_at, model, working_dir
        """
        sessions = []

        for path in sorted(
            self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                sessions.append(
                    {
                        "name": data.get("name", path.stem),
                        "created_at": data.get("created_at", "Unknown"),
                        "model": data.get("model", "Unknown"),
                        "working_dir": data.get("working_dir", "Unknown"),
                        "message_count": len(data.get("history", [])),
                        "path": str(path),
                    }
                )
            except Exception:
                # Include even if we can't parse it fully
                sessions.append(
                    {
                        "name": path.stem,
                        "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                        "model": "Unknown",
                        "working_dir": "Unknown",
                        "message_count": 0,
                        "path": str(path),
                    }
                )

        return sessions

    def delete(self, name: str) -> tuple[bool, str]:
        """
        Delete a saved session.

        Args:
            name: Session name

        Returns:
            (success, message) tuple
        """
        path = self._get_session_path(name)

        if not path.exists():
            return False, f"Session not found: {name}"

        try:
            path.unlink()
            return True, f"Deleted session: {name}"
        except Exception as e:
            return False, f"Failed to delete session: {e}"

    def auto_save(
        self,
        history: List[Dict[str, Any]],
        model: str,
        working_dir: str,
        auto_approve: bool = False,
    ) -> tuple[bool, str]:
        """
        Auto-save session with timestamp-based name.

        Returns:
            (success, message) tuple
        """
        # Generate name from working directory and timestamp
        dir_name = Path(working_dir).name or "session"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{dir_name}-{timestamp}"

        return self.save(name, history, model, working_dir, auto_approve, {"auto_saved": True})

    def get_latest(self, working_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get the most recent session, optionally filtered by working directory.

        Args:
            working_dir: Filter by working directory

        Returns:
            Session data dict or None
        """
        sessions = self.list_sessions()

        if working_dir:
            sessions = [s for s in sessions if s.get("working_dir") == working_dir]

        if not sessions:
            return None

        # Return the most recent
        success, data = self.load(sessions[0]["name"])
        return data if success else None
