"""Credential storage.

Credentials (session cookies, user token, OAuth tokens) are stored per profile in
the OS keyring when one is usable, otherwise in a 0600 JSON file under the state
directory. On WSL there is usually no Secret Service running, so the file store
is the common case; the directory is created 0700.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import APP, state_dir


def _cred_path(profile: str) -> Path:
    return state_dir() / "credentials" / f"{profile}.json"


def _keyring():
    if os.environ.get("SNCLI_NO_KEYRING"):
        return None
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore

        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or "chainer" in type(kr).__module__ and not kr.backends:
            return None
        return keyring
    except Exception:
        return None


def load(profile: str) -> dict[str, Any] | None:
    kr = _keyring()
    if kr is not None:
        try:
            raw = kr.get_password(APP, profile)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    p = _cred_path(profile)
    if p.exists():
        return json.loads(p.read_text())
    return None


def save(profile: str, creds: dict[str, Any]) -> str:
    """Persist credentials; returns a description of where they went."""
    raw = json.dumps(creds)
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(APP, profile, raw)
            return "system keyring"
        except Exception:
            pass
    p = _cred_path(profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(raw)
    os.replace(tmp, p)
    return str(p)


def delete(profile: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(APP, profile)
        except Exception:
            pass
    p = _cred_path(profile)
    if p.exists():
        p.unlink()
