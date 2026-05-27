"""
Configuration management for Circuit Agent.
Handles credentials, settings, and CIRCUIT.md loading.
"""

import json
import os
import warnings
from typing import Any, Dict, Optional, Tuple

# Optional keyring support for secure credential storage
try:
    import keyring

    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False

# Optional certifi for CA bundle
try:
    import certifi

    CERTIFI_AVAILABLE = True
except ImportError:
    CERTIFI_AVAILABLE = False


# Custom warning classes
class SecurityWarning(UserWarning):
    """Warning for security-related issues."""

    pass


# Configuration paths
CONFIG_DIR = os.path.expanduser("~/.config/circuit-agent")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
GLOBAL_CIRCUIT_MD = os.path.join(CONFIG_DIR, "CIRCUIT.md")
KEYRING_SERVICE = "circuit-agent"

# API Configuration
TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
CHAT_BASE_URL = "https://chat-ai.cisco.com/openai/deployments"
API_VERSION = "2025-04-01-preview"

# Available models (entitled by this VPS's coding-tier appkey)
MODELS = {
    "1": ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite - Fast & cheap"),
    "2": ("gpt-5-nano", "GPT-5 Nano - Reasoning model (uses CoT tokens)"),
}


# SSL/TLS Configuration
class SSLConfig:
    """SSL/TLS configuration for API connections."""

    def __init__(self):
        self._verify: bool | str = True
        self._warned: bool = False

    @property
    def verify(self) -> bool | str:
        """Get SSL verification setting. Returns True, False, or path to CA bundle."""
        return self._verify

    def get_verify_param(self) -> bool | str:
        """Get the verification parameter for httpx."""
        if self._verify is True and CERTIFI_AVAILABLE:
            return certifi.where()
        return self._verify

    def disable_verification(self, warn: bool = True) -> None:
        """
        Disable SSL verification (NOT RECOMMENDED).
        Use only for corporate proxies that intercept SSL.
        """
        self._verify = False
        if warn and not self._warned:
            self._warned = True
            warnings.warn(
                "SSL verification disabled! This is insecure and should only be used "
                "for corporate proxies. Set CIRCUIT_SSL_VERIFY=true to re-enable.",
                SecurityWarning,
                stacklevel=2,
            )

    def enable_verification(self, ca_bundle: str = None) -> None:
        """Enable SSL verification with optional custom CA bundle."""
        if ca_bundle:
            if not os.path.exists(ca_bundle):
                raise ValueError(f"CA bundle not found: {ca_bundle}")
            self._verify = ca_bundle
        else:
            self._verify = True


# Global SSL config instance
ssl_config = SSLConfig()

# Check environment for SSL override
if os.environ.get("CIRCUIT_SSL_VERIFY", "").lower() in ("false", "0", "no"):
    ssl_config.disable_verification()
elif os.environ.get("CIRCUIT_CA_BUNDLE"):
    ssl_config.enable_verification(os.environ.get("CIRCUIT_CA_BUNDLE"))


# Dangerous command patterns to warn about
DANGEROUS_PATTERNS = [
    # Destructive file operations
    r"rm\s+(-rf?|--recursive).*(/|~|\$HOME)",
    r"rm\s+-rf?\s+\.",
    r"rm\s+-rf?\s+/\w",
    r"mv\s+/\w",
    r">\s*/dev/sd",
    r">\s*/etc/",
    # Privileged operations
    r"sudo\s+rm",
    r"sudo\s+mv\s+/",
    r"sudo\s+chmod",
    r"sudo\s+chown",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R.*\s+/",
    # System operations
    r"mkfs\.",
    r"dd\s+.*of=/dev/",
    r"shutdown",
    r"reboot",
    r":(){ :\|:& };:",  # Fork bomb
    # Git dangerous operations
    r"git\s+push.*--force",
    r"git\s+push.*-f\b",
    r"git\s+reset\s+--hard",
    # Remote code execution
    r"curl.*\|\s*(ba)?sh",
    r"wget.*\|\s*(ba)?sh",
    # Command injection patterns (additional protection)
    r"\$\([^)]+\)",  # $(command) substitution
    r"`[^`]+`",  # `command` substitution
    r"\$\{[^}]+\}",  # ${var} expansion with commands
    r";\s*rm\s",  # Command chaining with rm
    r"&&\s*rm\s",  # Logical AND with rm
    r"\|\|\s*rm\s",  # Logical OR with rm
    r">\s*/dev/null\s*2>&1\s*&",  # Background execution hiding output
    # Reverse shells and network exfiltration
    r"nc\s+-[el]",  # netcat listener
    r"bash\s+-i\s+>&\s*/dev/tcp",  # bash reverse shell
    r"/dev/tcp/",  # bash network device
    r"python.*socket.*connect",  # Python socket connections
    r"base64\s+-d.*\|\s*(ba)?sh",  # Base64 decode to shell
]


def _load_credentials_from_keyring() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Load credentials from system keyring (secure storage)."""
    if not KEYRING_AVAILABLE:
        return None, None, None

    try:
        client_id = keyring.get_password(KEYRING_SERVICE, "client_id")
        client_secret = keyring.get_password(KEYRING_SERVICE, "client_secret")
        app_key = keyring.get_password(KEYRING_SERVICE, "app_key")
        return client_id, client_secret, app_key
    except Exception:
        return None, None, None


def _load_credentials_from_file() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Load credentials from config file."""
    if not os.path.exists(CONFIG_FILE):
        return None, None, None

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            return (config.get("client_id"), config.get("client_secret"), config.get("app_key"))
    except (json.JSONDecodeError, IOError):
        return None, None, None


def load_credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Load credentials from available sources.

    Priority order:
    1. Environment variables (highest priority)
    2. System keyring (secure storage, if available)
    3. Config file (fallback)

    Returns (client_id, client_secret, app_key) - any can be None if not found.
    """
    client_id = None
    client_secret = None
    app_key = None

    # Try keyring first (more secure than file)
    kr_id, kr_secret, kr_key = _load_credentials_from_keyring()
    if kr_id and kr_secret and kr_key:
        client_id, client_secret, app_key = kr_id, kr_secret, kr_key

    # Fall back to config file
    if not (client_id and client_secret and app_key):
        file_id, file_secret, file_key = _load_credentials_from_file()
        client_id = client_id or file_id
        client_secret = client_secret or file_secret
        app_key = app_key or file_key

    # Environment variables override everything
    if os.environ.get("CIRCUIT_CLIENT_ID"):
        client_id = os.environ.get("CIRCUIT_CLIENT_ID")
    if os.environ.get("CIRCUIT_CLIENT_SECRET"):
        client_secret = os.environ.get("CIRCUIT_CLIENT_SECRET")
    if os.environ.get("CIRCUIT_APP_KEY"):
        app_key = os.environ.get("CIRCUIT_APP_KEY")

    return client_id, client_secret, app_key


def save_credentials(
    client_id: str, client_secret: str, app_key: str, use_keyring: bool = True
) -> Tuple[bool, str]:
    """
    Save credentials securely.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        app_key: Circuit app key
        use_keyring: If True and available, use system keyring (more secure)

    Returns:
        Tuple of (success, storage_method) where storage_method is 'keyring' or 'file'
    """
    # Try keyring first if requested and available
    if use_keyring and KEYRING_AVAILABLE:
        try:
            keyring.set_password(KEYRING_SERVICE, "client_id", client_id)
            keyring.set_password(KEYRING_SERVICE, "client_secret", client_secret)
            keyring.set_password(KEYRING_SERVICE, "app_key", app_key)
            return True, "keyring"
        except Exception:
            pass  # Fall through to file-based storage

    # Fall back to config file
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        config = {"client_id": client_id, "client_secret": client_secret, "app_key": app_key}
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        # Secure the file (readable only by owner)
        os.chmod(CONFIG_FILE, 0o600)
        return True, "file"
    except IOError:
        return False, "none"


def delete_credentials() -> Tuple[bool, str]:
    """
    Delete saved credentials from all storage locations.

    Returns:
        Tuple of (success, message)
    """
    deleted = []

    # Delete from keyring
    if KEYRING_AVAILABLE:
        try:
            for key in ["client_id", "client_secret", "app_key"]:
                try:
                    keyring.delete_password(KEYRING_SERVICE, key)
                except keyring.errors.PasswordDeleteError:
                    pass
            deleted.append("keyring")
        except Exception:
            pass

    # Delete config file
    try:
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)
            deleted.append("file")
    except IOError:
        pass

    if deleted:
        return True, f"Deleted from: {', '.join(deleted)}"
    return False, "No credentials found to delete"


# =========================================================================
# Anthropic/Claude API Support
# =========================================================================

ANTHROPIC_MODELS = {
    "1": ("claude-sonnet-4-20250514", "Claude Sonnet 4 - Fast & capable"),
    "2": ("claude-opus-4-20250514", "Claude Opus 4 - Most capable"),
    "3": ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet - Balanced"),
    "4": ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku - Fast & efficient"),
}


def load_anthropic_key() -> Optional[str]:
    """
    Load Anthropic API key from available sources.

    Priority order:
    1. Environment variable ANTHROPIC_API_KEY
    2. System keyring
    3. Config file
    """
    # Environment variable first
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ.get("ANTHROPIC_API_KEY")

    # Try keyring
    if KEYRING_AVAILABLE:
        try:
            key = keyring.get_password(KEYRING_SERVICE, "anthropic_api_key")
            if key:
                return key
        except Exception:
            pass

    # Fall back to config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("anthropic_api_key")
        except (json.JSONDecodeError, IOError):
            pass

    return None


def save_anthropic_key(api_key: str, use_keyring: bool = True) -> Tuple[bool, str]:
    """
    Save Anthropic API key securely.

    Returns:
        Tuple of (success, storage_method)
    """
    # Try keyring first
    if use_keyring and KEYRING_AVAILABLE:
        try:
            keyring.set_password(KEYRING_SERVICE, "anthropic_api_key", api_key)
            return True, "keyring"
        except Exception:
            pass

    # Fall back to config file
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

        # Load existing config or create new
        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        config["anthropic_api_key"] = api_key

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)
        return True, "file"
    except IOError:
        return False, "none"


def load_provider_preference() -> str:
    """Load the preferred AI provider (cisco or anthropic)."""
    # Check environment
    if os.environ.get("CIRCUIT_PROVIDER"):
        return os.environ.get("CIRCUIT_PROVIDER")

    # Check config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("provider", "cisco")
        except (json.JSONDecodeError, IOError):
            pass

    return "cisco"


def save_provider_preference(provider: str) -> bool:
    """Save the preferred AI provider."""
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        config["provider"] = provider

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except IOError:
        return False


def get_credential_storage_info() -> Dict[str, Any]:
    """Get information about credential storage."""
    return {
        "keyring_available": KEYRING_AVAILABLE,
        "keyring_service": KEYRING_SERVICE if KEYRING_AVAILABLE else None,
        "config_file": CONFIG_FILE,
        "config_file_exists": os.path.exists(CONFIG_FILE),
        "ssl_verification": ssl_config.verify,
        "certifi_available": CERTIFI_AVAILABLE,
        "provider": load_provider_preference(),
        "has_anthropic_key": load_anthropic_key() is not None,
    }


def load_circuit_md(working_dir: str) -> Optional[str]:
    """
    Load CIRCUIT.md configuration file.
    Checks (in order):
    1. Working directory: ./CIRCUIT.md
    2. Global config: ~/.config/circuit-agent/CIRCUIT.md

    Returns the content or None if not found.
    """
    # Check project-specific first
    project_circuit_md = os.path.join(working_dir, "CIRCUIT.md")
    if os.path.exists(project_circuit_md):
        try:
            with open(project_circuit_md, "r", encoding="utf-8") as f:
                return f.read()
        except IOError:
            pass

    # Fall back to global
    if os.path.exists(GLOBAL_CIRCUIT_MD):
        try:
            with open(GLOBAL_CIRCUIT_MD, "r", encoding="utf-8") as f:
                return f.read()
        except IOError:
            pass

    return None


def get_circuit_md_locations(working_dir: str) -> Dict[str, bool]:
    """Get the locations and existence status of CIRCUIT.md files."""
    return {
        "project": os.path.exists(os.path.join(working_dir, "CIRCUIT.md")),
        "global": os.path.exists(GLOBAL_CIRCUIT_MD),
        "project_path": os.path.join(working_dir, "CIRCUIT.md"),
        "global_path": GLOBAL_CIRCUIT_MD,
    }


def detect_project_type(working_dir: str) -> str:
    """Detect project type and return context string."""
    info_parts = []

    # Check for common project files
    checks = [
        ("package.json", "Node.js/JavaScript project"),
        ("pyproject.toml", "Python project (pyproject.toml)"),
        ("setup.py", "Python project (setup.py)"),
        ("requirements.txt", "Python project"),
        ("Cargo.toml", "Rust project"),
        ("go.mod", "Go project"),
        ("pom.xml", "Java/Maven project"),
        ("build.gradle", "Java/Gradle project"),
        ("Gemfile", "Ruby project"),
        ("composer.json", "PHP project"),
        ("Makefile", "Project with Makefile"),
        ("Dockerfile", "Docker containerized"),
        (".git", "Git repository"),
    ]

    for filename, description in checks:
        if os.path.exists(os.path.join(working_dir, filename)):
            info_parts.append(description)

    if info_parts:
        return "**Project detected**: " + ", ".join(info_parts[:3])
    return ""


def get_config_summary() -> Dict[str, Any]:
    """Get a summary of current configuration."""
    client_id, client_secret, app_key = load_credentials()

    return {
        "credentials_saved": os.path.exists(CONFIG_FILE),
        "config_dir": CONFIG_DIR,
        "config_file": CONFIG_FILE,
        "global_circuit_md": os.path.exists(GLOBAL_CIRCUIT_MD),
        "has_credentials": bool(client_id and client_secret and app_key),
        "client_id_preview": client_id[:8] + "..." if client_id else None,
    }


# =========================================================================
# MCP (Model Context Protocol) Configuration
# =========================================================================

MCP_CONFIG_FILE = os.path.join(CONFIG_DIR, "mcp_servers.json")


def load_github_pat() -> Optional[str]:
    """
    Load GitHub Personal Access Token from available sources.

    Priority order:
    1. Environment variable GITHUB_PERSONAL_ACCESS_TOKEN
    2. System keyring
    3. Config file
    """
    # Environment variable first
    if os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        return os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")

    # Also check GITHUB_TOKEN as alternative
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ.get("GITHUB_TOKEN")

    # Try keyring
    if KEYRING_AVAILABLE:
        try:
            key = keyring.get_password(KEYRING_SERVICE, "github_pat")
            if key:
                return key
        except Exception:
            pass

    # Fall back to config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("github_pat")
        except (json.JSONDecodeError, IOError):
            pass

    return None


def save_github_pat(pat: str, use_keyring: bool = True) -> Tuple[bool, str]:
    """
    Save GitHub Personal Access Token securely.

    Args:
        pat: GitHub Personal Access Token
        use_keyring: If True and available, use system keyring

    Returns:
        Tuple of (success, storage_method)
    """
    # Try keyring first
    if use_keyring and KEYRING_AVAILABLE:
        try:
            keyring.set_password(KEYRING_SERVICE, "github_pat", pat)
            return True, "keyring"
        except Exception:
            pass

    # Fall back to config file
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

        config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        config["github_pat"] = pat

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        os.chmod(CONFIG_FILE, 0o600)
        return True, "file"
    except IOError:
        return False, "none"


def delete_github_pat() -> bool:
    """Delete GitHub PAT from storage."""
    deleted = False

    # Delete from keyring
    if KEYRING_AVAILABLE:
        try:
            keyring.delete_password(KEYRING_SERVICE, "github_pat")
            deleted = True
        except Exception:
            pass

    # Delete from config file
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
            if "github_pat" in config:
                del config["github_pat"]
                with open(CONFIG_FILE, "w") as f:
                    json.dump(config, f, indent=2)
                deleted = True
        except (json.JSONDecodeError, IOError):
            pass

    return deleted


def load_mcp_servers() -> Dict[str, Any]:
    """
    Load MCP server configurations.

    Returns:
        Dictionary with server configurations
    """
    if not os.path.exists(MCP_CONFIG_FILE):
        return {"servers": {}}

    try:
        with open(MCP_CONFIG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"servers": {}}


def save_mcp_servers(config: Dict[str, Any]) -> bool:
    """
    Save MCP server configurations.

    Args:
        config: Dictionary with server configurations

    Returns:
        True if saved successfully
    """
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

        with open(MCP_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        os.chmod(MCP_CONFIG_FILE, 0o600)
        return True
    except IOError:
        return False


def load_github_mcp_config() -> Dict[str, Any]:
    """
    Load GitHub MCP server configuration.

    Returns:
        Dictionary with GitHub MCP settings
    """
    mcp_config = load_mcp_servers()
    return mcp_config.get("servers", {}).get(
        "github",
        {
            "enabled": False,
            "toolsets": ["repos", "issues", "pull_requests", "actions"],
            "use_remote": True,
        },
    )


def save_github_mcp_config(enabled: bool, toolsets: list, use_remote: bool = True) -> bool:
    """
    Save GitHub MCP server configuration.

    Args:
        enabled: Whether GitHub MCP is enabled
        toolsets: List of enabled toolsets
        use_remote: Use remote server (True) or Docker (False)

    Returns:
        True if saved successfully
    """
    mcp_config = load_mcp_servers()

    if "servers" not in mcp_config:
        mcp_config["servers"] = {}

    mcp_config["servers"]["github"] = {
        "enabled": enabled,
        "toolsets": toolsets,
        "use_remote": use_remote,
    }

    return save_mcp_servers(mcp_config)


# =========================================================================
# UI Settings Persistence
# =========================================================================

UI_SETTINGS_FILE = os.path.join(CONFIG_DIR, "ui_settings.json")

DEFAULT_UI_SETTINGS = {
    "theme": "dark",
    "font_family": "Consolas, Monaco, monospace",
    "font_size": 13,
    "tab_size": 4,
    "use_spaces": True,
    "word_wrap": False,
    "show_minimap": True,
    "show_line_numbers": True,
    "auto_save": False,
    "auto_save_interval": 60,  # seconds
    "terminal_font_size": 12,
    "window_geometry": None,  # QByteArray stored as hex string
    "recent_projects": [],
    "last_project": None,
}


def load_ui_settings() -> Dict[str, Any]:
    """
    Load UI settings from settings file.

    Returns:
        Dictionary with UI settings, using defaults for missing keys
    """
    settings = DEFAULT_UI_SETTINGS.copy()

    if os.path.exists(UI_SETTINGS_FILE):
        try:
            with open(UI_SETTINGS_FILE, "r") as f:
                saved = json.load(f)
                settings.update(saved)
        except (json.JSONDecodeError, IOError):
            pass

    return settings


def save_ui_settings(settings: Dict[str, Any]) -> bool:
    """
    Save UI settings to settings file.

    Args:
        settings: Dictionary with UI settings to save

    Returns:
        True if saved successfully
    """
    try:
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)

        # Merge with existing settings to preserve any additional keys
        existing = load_ui_settings()
        existing.update(settings)

        with open(UI_SETTINGS_FILE, "w") as f:
            json.dump(existing, f, indent=2)
        return True
    except IOError:
        return False


def update_ui_setting(key: str, value: Any) -> bool:
    """
    Update a single UI setting.

    Args:
        key: Setting key to update
        value: New value for the setting

    Returns:
        True if saved successfully
    """
    settings = load_ui_settings()
    settings[key] = value
    return save_ui_settings(settings)


def reset_ui_settings() -> bool:
    """
    Reset UI settings to defaults.

    Returns:
        True if reset successfully
    """
    return save_ui_settings(DEFAULT_UI_SETTINGS)
