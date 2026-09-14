"""Persistent ScreenCast permission and restore token management for Qubit 4.0.

Provides secure local persistence for XDG Desktop Portal ScreenCast restore tokens,
allowing Qubit to restore authorized ScreenCast sessions across runs without
repeated user authorization prompts.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = Path.home() / ".config" / "qubit"
DEFAULT_TOKEN_FILE = DEFAULT_TOKEN_DIR / "screencast_restore_token"


def get_token_file_path() -> Path:
    """Return the configured Path to the ScreenCast restore token file."""
    custom_path = os.getenv("QUBIT_SCREENCAST_TOKEN_FILE")
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    return DEFAULT_TOKEN_FILE


def load_restore_token() -> Optional[str]:
    """Load the stored ScreenCast restore token from secure local storage.

    Returns:
        Optional[str]: The token string if available and non-empty, else None.
    """
    token_path = get_token_file_path()
    try:
        if token_path.is_file():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                logger.debug(f"Loaded ScreenCast restore token from {token_path}")
                return token
    except Exception as e:
        logger.warning(f"Could not read ScreenCast restore token from {token_path}: {e}")
    return None


def save_restore_token(token: str) -> None:
    """Save the ScreenCast restore token to secure local storage with restricted permissions.

    Args:
        token: Non-empty restore token returned by the portal.
    """
    if not token or not isinstance(token, str):
        return

    token_str = token.strip()
    if not token_str:
        return

    token_path = get_token_file_path()
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(token_path.parent, 0o700)
        except OSError:
            pass

        # Write with 0o600 permissions
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(token_path), flags, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(token_str)

        logger.info(f"ScreenCast restore token saved successfully to {token_path}")
    except Exception as e:
        logger.warning(f"Failed to save ScreenCast restore token to {token_path}: {e}")


def clear_restore_token() -> None:
    """Remove the stored ScreenCast restore token (e.g. after revocation or error)."""
    token_path = get_token_file_path()
    try:
        if token_path.exists():
            token_path.unlink()
            logger.info(f"Cleared ScreenCast restore token at {token_path}")
    except Exception as e:
        logger.warning(f"Failed to clear ScreenCast restore token at {token_path}: {e}")
