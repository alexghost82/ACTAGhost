"""Identity primitives used by API and channel entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str
    role: Role
