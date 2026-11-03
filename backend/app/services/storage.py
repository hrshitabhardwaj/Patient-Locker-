"""Encrypted file storage for patient documents.

Files are encrypted with AES-256-GCM before they touch disk and are named by document ID
only, so the storage folder reveals neither content nor patient names. The document ID is
bound in as associated data: a stored file cannot be swapped onto another record without
decryption failing.
"""

import base64
import os
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_NONCE_BYTES = 12


class StorageIntegrityError(Exception):
    """The stored file was altered, belongs to another record, or the key is wrong."""


class EncryptedFileStore:
    def __init__(self, root: Path, key: bytes):
        self._root = root
        self._aead = AESGCM(key)

    def _path(self, storage_key: str) -> Path:
        if not storage_key.isalnum():
            raise ValueError("storage keys are alphanumeric")
        return self._root / "documents" / storage_key[:2] / storage_key

    def put(self, storage_key: str, data: bytes, *, associated_data: bytes) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        blob = nonce + self._aead.encrypt(nonce, data, associated_data)

        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".partial")
        with open(temporary, "wb", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def get(self, storage_key: str, *, associated_data: bytes) -> bytes:
        blob = self._path(storage_key).read_bytes()
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            return self._aead.decrypt(nonce, ciphertext, associated_data)
        except InvalidTag:
            raise StorageIntegrityError(storage_key) from None

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)


@lru_cache
def get_file_store() -> EncryptedFileStore:
    settings = get_settings()
    key = base64.urlsafe_b64decode(settings.file_encryption_key.get_secret_value())
    return EncryptedFileStore(settings.storage_dir, key)
