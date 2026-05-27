"""
Security module for Circuit Agent v4.0.

Provides:
- Secret detection and redaction
- Audit logging for all agent actions
- Security scanning for files
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SecretDetector:
    """Detect and optionally redact secrets in content."""

    # Patterns for common secrets (pattern, name, severity)
    PATTERNS = [
        # API Keys and Tokens
        (r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{20,})["\']?', "API Key", "high"),
        (
            r'(?i)(secret[_-]?key|secretkey)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{20,})["\']?',
            "Secret Key",
            "high",
        ),
        (
            r'(?i)(access[_-]?token|accesstoken)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{20,})["\']?',
            "Access Token",
            "high",
        ),
        # Passwords
        (r'(?i)(password|passwd|pwd)\s*[:=]\s*["\']([^"\']{4,})["\']', "Password", "critical"),
        (r'(?i)(password|passwd|pwd)\s*[:=]\s*([^\s"\']{8,})', "Password", "critical"),
        # Cloud Provider Tokens
        (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID", "critical"),
        (
            r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*["\']?([a-zA-Z0-9/+=]{40})["\']?',
            "AWS Secret Key",
            "critical",
        ),
        (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token", "critical"),
        (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token", "critical"),
        (r"github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}", "GitHub Fine-Grained PAT", "critical"),
        (r"sk-[a-zA-Z0-9]{48}", "OpenAI API Key", "critical"),
        (r"sk-proj-[a-zA-Z0-9\-_]{48,}", "OpenAI Project API Key", "critical"),
        (r"xox[baprs]-[a-zA-Z0-9\-]{10,}", "Slack Token", "high"),
        # Private Keys
        (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "Private Key", "critical"),
        (r"-----BEGIN PGP PRIVATE KEY BLOCK-----", "PGP Private Key", "critical"),
        # Database URLs
        (
            r'(?i)(mongodb|postgres|mysql|redis)://[^\s<>"\']+:[^\s<>"\']+@[^\s<>"\']+',
            "Database URL with Credentials",
            "critical",
        ),
        # Bearer Tokens
        (r"(?i)bearer\s+([a-zA-Z0-9\-_.]{20,})", "Bearer Token", "high"),
        (r"(?i)authorization:\s*bearer\s+([a-zA-Z0-9\-_.]{20,})", "Authorization Header", "high"),
        # Generic Secrets
        (
            r'(?i)(client[_-]?secret|clientsecret)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{16,})["\']?',
            "Client Secret",
            "high",
        ),
        (
            r'(?i)(auth[_-]?token|authtoken)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{20,})["\']?',
            "Auth Token",
            "high",
        ),
        # Cisco-specific
        (
            r'(?i)circuit[_-]?client[_-]?(id|secret)\s*[:=]\s*["\']?([a-zA-Z0-9\-_]{16,})["\']?',
            "Circuit Credential",
            "high",
        ),
    ]

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._compiled_patterns = [
            (re.compile(pattern), name, severity) for pattern, name, severity in self.PATTERNS
        ]

    def scan(self, content: str) -> List[Dict[str, Any]]:
        """
        Scan content for potential secrets.

        Returns list of findings with type, severity, line number, and preview.
        """
        if not self.enabled:
            return []

        findings = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, 1):
            for pattern, secret_type, severity in self._compiled_patterns:
                matches = pattern.finditer(line)
                for match in matches:
                    # Get the matched secret (usually in a capture group)
                    secret = match.group(0)
                    preview = self._create_preview(secret)

                    findings.append(
                        {
                            "type": secret_type,
                            "severity": severity,
                            "line": line_num,
                            "preview": preview,
                            "match_start": match.start(),
                            "match_end": match.end(),
                        }
                    )

        # Deduplicate by type and line
        seen = set()
        unique_findings = []
        for f in findings:
            key = (f["type"], f["line"], f["preview"])
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        return unique_findings

    def _create_preview(self, secret: str, visible_chars: int = 8) -> str:
        """Create a safe preview of a secret (first few chars + ***)."""
        if len(secret) <= visible_chars:
            return "*" * len(secret)
        return secret[:visible_chars] + "***"

    def redact(self, content: str) -> Tuple[str, int]:
        """
        Redact all detected secrets from content.

        Returns (redacted_content, count_of_redactions).
        """
        if not self.enabled:
            return content, 0

        redaction_count = 0
        for pattern, secret_type, _ in self._compiled_patterns:
            new_content, count = pattern.subn(f"[REDACTED:{secret_type}]", content)
            redaction_count += count
            content = new_content

        return content, redaction_count

    def has_secrets(self, content: str) -> bool:
        """Quick check if content contains any secrets."""
        if not self.enabled:
            return False
        return len(self.scan(content)) > 0

    def format_findings(self, findings: List[Dict]) -> str:
        """Format findings for display."""
        if not findings:
            return "No secrets detected."

        output = []
        critical = [f for f in findings if f["severity"] == "critical"]
        high = [f for f in findings if f["severity"] == "high"]

        if critical:
            output.append(f"\n  CRITICAL ({len(critical)} found):")
            for f in critical[:5]:  # Limit display
                output.append(f"    Line {f['line']}: {f['type']} - {f['preview']}")

        if high:
            output.append(f"\n  HIGH ({len(high)} found):")
            for f in high[:5]:
                output.append(f"    Line {f['line']}: {f['type']} - {f['preview']}")

        if len(findings) > 10:
            output.append(f"\n  ... and {len(findings) - 10} more")

        return "\n".join(output)


class AuditLogger:
    """Log all agent actions for audit trail."""

    def __init__(self, log_dir: Optional[str] = None, enabled: bool = True):
        self.enabled = enabled
        self._secret_detector = SecretDetector()  # For redacting sensitive data in logs

        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            self.log_dir = Path.home() / ".config" / "circuit-agent" / "audit"

        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.session_log = self.log_dir / f"session-{self.session_id}.jsonl"
            self._log_count = 0

    def _redact_dict(self, d: Dict) -> Dict:
        """Recursively redact secrets from a dictionary."""
        result = {}
        for key, value in d.items():
            if isinstance(value, str):
                redacted, _ = self._secret_detector.redact(value)
                result[key] = redacted
            elif isinstance(value, dict):
                result[key] = self._redact_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self._redact_dict(item)
                    if isinstance(item, dict)
                    else (self._secret_detector.redact(item)[0] if isinstance(item, str) else item)
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def log(self, action: str, details: Dict[str, Any], success: bool = True):
        """Log an action to the audit trail."""
        if not self.enabled:
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "session": self.session_id,
            "action": action,
            "success": success,
            "details": details,
        }

        try:
            with open(self.session_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self._log_count += 1
        except Exception:
            pass  # Don't let logging failures break the agent

    def log_tool_call(self, tool_name: str, args: Dict, result: str, success: bool = True):
        """Log a tool call with secret redaction for security."""
        # Redact secrets from args
        redacted_args = self._redact_dict(args) if args else {}

        # Truncate and redact result
        result_preview = result[:500] + "..." if len(result) > 500 else result
        result_preview, _ = self._secret_detector.redact(result_preview)

        self.log(
            "tool_call",
            {
                "tool": tool_name,
                "args": redacted_args,
                "result_preview": result_preview,
            },
            success,
        )

    def log_api_call(self, model: str, prompt_tokens: int, completion_tokens: int):
        """Log an API call."""
        self.log(
            "api_call",
            {
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )

    def log_user_input(self, input_preview: str):
        """Log user input (truncated for privacy)."""
        preview = input_preview[:100] + "..." if len(input_preview) > 100 else input_preview
        self.log("user_input", {"preview": preview})

    def log_file_operation(self, operation: str, path: str, success: bool = True):
        """Log a file operation."""
        self.log(
            "file_operation",
            {
                "operation": operation,
                "path": path,
            },
            success,
        )

    def log_error(self, error_type: str, message: str, context: Optional[Dict] = None):
        """Log an error."""
        self.log(
            "error",
            {
                "error_type": error_type,
                "message": message,
                "context": context or {},
            },
            success=False,
        )

    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics for current session."""
        if not self.enabled or not self.session_log.exists():
            return {"enabled": False}

        stats = {
            "session_id": self.session_id,
            "log_file": str(self.session_log),
            "entries": self._log_count,
            "enabled": True,
        }

        # Count by action type
        action_counts: Dict[str, int] = {}
        try:
            with open(self.session_log, "r") as f:
                for line in f:
                    entry = json.loads(line)
                    action = entry.get("action", "unknown")
                    action_counts[action] = action_counts.get(action, 0) + 1
            stats["action_counts"] = action_counts
        except Exception:
            pass

        return stats

    def get_recent_entries(self, count: int = 10) -> List[Dict]:
        """Get recent audit log entries."""
        if not self.enabled or not self.session_log.exists():
            return []

        entries = []
        try:
            with open(self.session_log, "r") as f:
                for line in f:
                    entries.append(json.loads(line))
            return entries[-count:]
        except Exception:
            return []

    def list_sessions(self, limit: int = 10) -> List[Dict]:
        """List recent audit sessions."""
        if not self.enabled:
            return []

        sessions = []
        for log_file in sorted(self.log_dir.glob("session-*.jsonl"), reverse=True)[:limit]:
            try:
                stat = log_file.stat()
                sessions.append(
                    {
                        "session_id": log_file.stem.replace("session-", ""),
                        "file": str(log_file),
                        "size_kb": stat.st_size / 1024,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
            except Exception:
                pass

        return sessions


class CostTracker:
    """Track API costs for the session."""

    # Cost per 1K tokens (approximate, as of 2024)
    COST_PER_1K_TOKENS = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-4.1": {"input": 0.01, "output": 0.03},
        "o4-mini": {"input": 0.0011, "output": 0.0044},
    }

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls_by_model: Dict[str, Dict[str, int]] = {}

    def track(self, model: str, input_tokens: int, output_tokens: int):
        """Track tokens for a single API call."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

        if model not in self.calls_by_model:
            self.calls_by_model[model] = {"input": 0, "output": 0, "calls": 0}

        self.calls_by_model[model]["input"] += input_tokens
        self.calls_by_model[model]["output"] += output_tokens
        self.calls_by_model[model]["calls"] += 1

    def get_cost(self, model: Optional[str] = None) -> float:
        """Get estimated cost in USD."""
        total_cost = 0.0

        for m, tokens in self.calls_by_model.items():
            if model and m != model:
                continue

            rates = self.COST_PER_1K_TOKENS.get(m, {"input": 0.005, "output": 0.015})
            cost = (tokens["input"] * rates["input"] + tokens["output"] * rates["output"]) / 1000
            total_cost += cost

        return total_cost

    def get_stats(self) -> Dict[str, Any]:
        """Get detailed cost statistics."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_cost_usd": round(self.get_cost(), 4),
            "by_model": {
                model: {**tokens, "cost_usd": round(self.get_cost(model), 4)}
                for model, tokens in self.calls_by_model.items()
            },
        }

    def format_stats(self) -> str:
        """Format stats for display."""
        stats = self.get_stats()
        lines = [
            f"Total tokens: {stats['total_tokens']:,} ({stats['total_input_tokens']:,} in / {stats['total_output_tokens']:,} out)",
            f"Estimated cost: ${stats['estimated_cost_usd']:.4f}",
        ]

        if len(stats["by_model"]) > 1:
            lines.append("\nBy model:")
            for model, data in stats["by_model"].items():
                lines.append(f"  {model}: {data['calls']} calls, ${data['cost_usd']:.4f}")

        return "\n".join(lines)
