"""
OS keyring wrapper for secret storage.

Priority order:
  1. OS keyring (Windows Credential Locker / macOS Keychain / Linux SecretService)
  2. SQLite settings table with a WARNING in logs (fallback if keyring unavailable)

Secrets never appear in logs. The fallback to SQLite is unencrypted and
emits a one-time warning to the log so the user knows. For a single-user
personal monitoring tool on a personal machine this is acceptable.

Keys stored:
  discord_webhook
  telegram_token
  telegram_chat_id
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_APP_NAME = "PokemonCenterMonitor"
_KEYRING_AVAILABLE: Optional[bool] = None  # lazily checked


def _keyring_ok() -> bool:
    global _KEYRING_AVAILABLE
    if _KEYRING_AVAILABLE is None:
        try:
            import keyring
            # Probe: attempt a no-op get to confirm the backend is functional.
            # Catch BaseException: broken native backends (e.g. pyo3 panics on
            # Linux when libsecret/cffi are misconfigured) raise non-Exception
            # subclasses that would bypass a bare `except Exception` guard.
            keyring.get_password(_APP_NAME, "_probe_")
            _KEYRING_AVAILABLE = True
        except BaseException as exc:
            logger.warning(
                "OS keyring unavailable (%s) — secrets stored in local DB (unencrypted). "
                "Install a working SecretService backend for full keyring support.",
                type(exc).__name__,
            )
            _KEYRING_AVAILABLE = False
    return _KEYRING_AVAILABLE


def set_secret(key: str, value: str) -> None:
    """Store a secret. Value is stripped; empty string deletes the key."""
    value = (value or "").strip()

    if _keyring_ok():
        import keyring
        if value:
            keyring.set_password(_APP_NAME, key, value)
        else:
            try:
                keyring.delete_password(_APP_NAME, key)
            except Exception:
                pass
    else:
        from app.storage.database import set_setting, get_setting
        set_setting(f"_secret_{key}", value)


def get_secret(key: str) -> Optional[str]:
    """Return secret value or None if not set."""
    if _keyring_ok():
        import keyring
        val = keyring.get_password(_APP_NAME, key)
        return val or None
    else:
        from app.storage.database import get_setting
        val = get_setting(f"_secret_{key}", "")
        return val or None


# Convenience accessors for the three secrets used by this app

def get_discord_webhook() -> Optional[str]:
    return get_secret("discord_webhook")

def set_discord_webhook(value: str) -> None:
    set_secret("discord_webhook", value)

def get_telegram_token() -> Optional[str]:
    return get_secret("telegram_token")

def set_telegram_token(value: str) -> None:
    set_secret("telegram_token", value)

def get_telegram_chat_id() -> Optional[str]:
    return get_secret("telegram_chat_id")

def set_telegram_chat_id(value: str) -> None:
    set_secret("telegram_chat_id", value)
