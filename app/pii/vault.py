"""Encrypted in-memory reverse-mapping table (FR-1.3, NFR-1.2).

The vault holds the only copy of what each placeholder stood for. Three
properties matter and are enforced here rather than by convention:

* **Memory only.** Nothing is written to disk. The mapping dies with the
  process, which is the behaviour FR-1.3 asks for — placeholders must not
  outlive the session that created them.
* **AES-256-GCM.** Note this is *not* Fernet: Fernet is AES-128-CBC and would
  miss the AES-256 bar NFR-1.2 sets. Each value gets a fresh 96-bit nonce.
* **Bound ciphertexts.** The placeholder is passed as additional authenticated
  data, so a ciphertext cannot be moved from one placeholder to another
  without the tag check failing.
"""

from __future__ import annotations

import base64
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings
from app.core.exceptions import MaskingError

_NONCE_BYTES = 12  # 96 bits, the GCM-recommended nonce length
_KEY_BYTES = 32  # AES-256


def generate_key() -> str:
    """Fresh base64-encoded AES-256 key."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _load_key(configured: str) -> bytes:
    """Decode the configured key, or mint an ephemeral one.

    An empty setting is the recommended production posture, not a degraded
    mode: a per-process key means a restart cryptographically destroys every
    outstanding mapping.
    """
    if not configured:
        return AESGCM.generate_key(bit_length=256)
    try:
        key = base64.b64decode(configured, validate=True)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise MaskingError("RASED_VAULT_KEY is not valid base64.") from exc
    if len(key) != _KEY_BYTES:
        raise MaskingError(
            f"RASED_VAULT_KEY must decode to {_KEY_BYTES} bytes (AES-256); "
            f"got {len(key)}."
        )
    return key


@dataclass(slots=True)
class _Envelope:
    nonce: bytes
    ciphertext: bytes
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionVault:
    """Placeholder -> original value, for the lifetime of one session."""

    def __init__(self, session_id: str, *, key: bytes | None = None, ttl_seconds: int | None = None):
        settings = get_settings()
        self.session_id = session_id
        self._key = key if key is not None else _load_key(settings.vault_key)
        self._aes = AESGCM(self._key)
        self._ttl = timedelta(seconds=ttl_seconds or settings.vault_ttl_seconds)
        self._entries: dict[str, _Envelope] = {}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def is_expired(self, envelope: _Envelope) -> bool:
        return datetime.now(UTC) - envelope.created_at > self._ttl

    def purge(self) -> None:
        """Drop every mapping. Called when a transaction closes."""
        with self._lock:
            self._entries.clear()

    def purge_expired(self) -> int:
        with self._lock:
            stale = [k for k, v in self._entries.items() if self.is_expired(v)]
            for key in stale:
                del self._entries[key]
            return len(stale)

    # -- storage -----------------------------------------------------------

    def store(self, placeholder: str, original: str) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = self._aes.encrypt(
            nonce,
            original.encode("utf-8"),
            placeholder.encode("utf-8"),  # AAD binds value to its placeholder
        )
        with self._lock:
            self._entries[placeholder] = _Envelope(nonce=nonce, ciphertext=ciphertext)

    def resolve(self, placeholder: str) -> str | None:
        """Decrypt one mapping, or ``None`` if unknown or expired."""
        with self._lock:
            envelope = self._entries.get(placeholder)
            if envelope is None:
                return None
            if self.is_expired(envelope):
                del self._entries[placeholder]
                return None

        try:
            plaintext = self._aes.decrypt(
                envelope.nonce,
                envelope.ciphertext,
                placeholder.encode("utf-8"),
            )
        except InvalidTag as exc:
            raise MaskingError(
                f"فشل التحقق من سلامة الحقل المشفر '{placeholder}'.",
                placeholder=placeholder,
            ) from exc
        return plaintext.decode("utf-8")

    def placeholders(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)


class VaultRegistry:
    """Process-local registry of live session vaults."""

    def __init__(self) -> None:
        self._vaults: dict[str, SessionVault] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> SessionVault:
        with self._lock:
            vault = self._vaults.get(session_id)
            if vault is None:
                vault = SessionVault(session_id)
                self._vaults[session_id] = vault
            return vault

    def get(self, session_id: str) -> SessionVault | None:
        with self._lock:
            return self._vaults.get(session_id)

    def discard(self, session_id: str) -> None:
        with self._lock:
            vault = self._vaults.pop(session_id, None)
        if vault is not None:
            vault.purge()

    def sweep(self) -> int:
        """Expire stale entries across every vault. Driven by a background task."""
        with self._lock:
            vaults = list(self._vaults.values())
        return sum(vault.purge_expired() for vault in vaults)


registry = VaultRegistry()
