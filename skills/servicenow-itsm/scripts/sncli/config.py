"""Profiles, paths and environment overrides."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP = "sncli"


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


def config_dir() -> Path:
    return Path(os.environ.get("SNCLI_CONFIG_DIR") or _xdg("XDG_CONFIG_HOME", ".config") / APP)


def state_dir() -> Path:
    return Path(os.environ.get("SNCLI_STATE_DIR") or _xdg("XDG_STATE_HOME", ".local/state") / APP)


def config_file() -> Path:
    return config_dir() / "config.json"


def normalize_instance(value: str) -> str:
    """Accept 'acme', 'acme.service-now.com' or a full URL; return 'https://host'."""
    v = value.strip().rstrip("/")
    if not v:
        raise ValueError("instance is empty")
    if "://" not in v:
        if "." not in v and ":" not in v:
            v = f"{v}.service-now.com"
        v = f"https://{v}"
    return v


@dataclass
class OAuthSettings:
    client_id: str | None = None
    client_secret: str | None = None  # only for confidential clients; prefer public+PKCE
    redirect_uri: str = "http://localhost:8765/callback"
    scope: str = ""


@dataclass
class Profile:
    name: str
    instance: str | None = None
    auth_method: str = "browser"  # browser | oauth | cookie
    sso_login_path: str | None = None  # e.g. "login_with_sso.do?glide_sso_id=<sys_id>"
    default_tables: list[str] = field(
        default_factory=lambda: ["incident", "sc_req_item", "change_request", "problem", "kb_knowledge"]
    )
    oauth: OAuthSettings = field(default_factory=OAuthSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "auth_method": self.auth_method,
            "sso_login_path": self.sso_login_path,
            "default_tables": self.default_tables,
            "oauth": {k: v for k, v in vars(self.oauth).items() if v is not None},
        }

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "Profile":
        p = cls(name=name)
        p.instance = d.get("instance")
        p.auth_method = d.get("auth_method", p.auth_method)
        p.sso_login_path = d.get("sso_login_path")
        p.default_tables = d.get("default_tables") or p.default_tables
        p.oauth = OAuthSettings(**(d.get("oauth") or {}))
        return p


class Config:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data or {"default_profile": "default", "profiles": {}}

    @classmethod
    def load(cls) -> "Config":
        f = config_file()
        if f.exists():
            return cls(json.loads(f.read_text()))
        return cls()

    def save(self) -> None:
        f = config_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(self.data, indent=2) + "\n")

    def profile(self, name: str | None = None) -> Profile:
        name = name or os.environ.get("SN_PROFILE") or self.data.get("default_profile") or "default"
        p = Profile.from_dict(name, self.data.get("profiles", {}).get(name, {}))
        if os.environ.get("SN_INSTANCE"):
            p.instance = normalize_instance(os.environ["SN_INSTANCE"])
        return p

    def put_profile(self, p: Profile, make_default: bool = False) -> None:
        self.data.setdefault("profiles", {})[p.name] = p.to_dict()
        if make_default or not self.data.get("default_profile"):
            self.data["default_profile"] = p.name
