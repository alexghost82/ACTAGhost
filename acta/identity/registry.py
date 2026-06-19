"""Credential-to-principal resolution for offline-first ACTA deployments."""

from __future__ import annotations

from dataclasses import dataclass

from acta.config import Settings
from acta.identity.models import Role, User


@dataclass(frozen=True)
class Session:
    principal: User


class IdentityRegistry:
    """Resolves API credentials to users and roles."""

    OFFLINE_DEFAULT_USER = User(user_id="default", display_name="Local Owner", role=Role.ADMIN)

    def __init__(self, users_by_token: dict[str, User]) -> None:
        self._users_by_token = dict(users_by_token)

    @classmethod
    def from_settings(cls, settings: Settings) -> "IdentityRegistry":
        users_by_token: dict[str, User] = {}
        for entry in _iter_api_user_entries(settings.api_users):
            token, user = cls._parse_api_user_entry(entry)
            users_by_token[token] = user
        # Backward-compatible legacy admin token.
        if settings.api_auth_token and settings.api_auth_token not in users_by_token:
            users_by_token[settings.api_auth_token] = User(
                user_id="default",
                display_name="Legacy Admin",
                role=Role.ADMIN,
            )
        return cls(users_by_token)

    @property
    def auth_configured(self) -> bool:
        return bool(self._users_by_token)

    def resolve(self, credential: str | None) -> Session | None:
        if not credential:
            return None
        user = self._users_by_token.get(credential)
        if user is None:
            return None
        return Session(principal=user)

    def default_session(self) -> Session:
        return Session(principal=self.OFFLINE_DEFAULT_USER)

    @staticmethod
    def _parse_api_user_entry(entry: str) -> tuple[str, User]:
        token, user_id, role_text = _split(entry)
        role = Role(role_text.lower())
        return token, User(user_id=user_id, display_name=user_id, role=role)


def _split(entry: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in entry.split(":")]
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            "ACTA_API_USERS entries must be token:user_id:role (SEC-8/SEC-4 hardening)"
        )
    return parts[0], parts[1], parts[2]


def _iter_api_user_entries(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]
