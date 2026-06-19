"""Local data-at-rest encryption using Fernet (AES-128-CBC + HMAC).

Key resolution order:
  1. ``ACTA_ENCRYPTION_KEY`` (a urlsafe base64 Fernet key)
  2. derived from ``ACTA_MASTER_PASSWORD`` via PBKDF2 with a persisted salt
  3. a freshly generated key persisted under the data directory (0600)
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from acta.config import Settings, get_settings
from acta.logging_config import get_logger

log = get_logger("security.crypto")


class Crypto:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._fernet = Fernet(self._resolve_key())

    def _resolve_key(self) -> bytes:
        s = self.settings
        if s.encryption_key:
            return s.encryption_key.encode()
        if s.master_password:
            return self._derive_from_password(s.master_password)
        return self._load_or_create_key()

    def _derive_from_password(self, password: str) -> bytes:
        salt_path = s_dir(self.settings) / "salt.bin"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)
            _chmod_600(salt_path)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def _load_or_create_key(self) -> bytes:
        key_path = self._key_path()
        if key_path.exists():
            return key_path.read_bytes()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        _chmod_600(key_path)
        log.info("Generated new local encryption key at %s", key_path)
        return key

    def _key_path(self):
        if self.settings.fernet_key_path:
            return self.settings.fernet_key_path.expanduser()
        return s_dir(self.settings) / "fernet.key"

    # -- API --------------------------------------------------------------- #
    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            log.error("Failed to decrypt token (wrong key?)")
            raise


def s_dir(settings: Settings):
    return settings.ensure_data_dir()


def _chmod_600(path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass
