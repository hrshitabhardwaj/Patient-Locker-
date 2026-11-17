"""Encrypted file storage for patient documents.

Files are encrypted with AES-256-GCM before they are stored and are named by document ID
only, so storage reveals neither content nor patient names. The document ID is bound in as
associated data: a stored file cannot be swapped onto another record without decryption
failing.

Two backends share that format (FILE_STORE in .env):
- disk: a folder on the server (local development).
- database: a table in PostgreSQL. For hosts whose disk is wiped on every restart, such as
  Render's free plan. Each call uses its own short transaction, exactly like a disk write,
  so callers behave the same with either backend.
"""

import base64
import os
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.models.storage import StoredFile

_NONCE_BYTES = 12


class StorageIntegrityError(Exception):
    """The stored file was altered, belongs to another record, or the key is wrong."""


def _check_key(storage_key: str) -> None:
    if not storage_key.isalnum():
        raise ValueError("storage keys are alphanumeric")


class _Sealer:
    def __init__(self, key: bytes):
        self._aead = AESGCM(key)

    def _seal(self, data: bytes, associated_data: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, data, associated_data)

    def _open(self, storage_key: str, blob: bytes, associated_data: bytes) -> bytes:
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            return self._aead.decrypt(nonce, ciphertext, associated_data)
        except InvalidTag:
            raise StorageIntegrityError(storage_key) from None


class EncryptedFileStore(_Sealer):
    """Files on disk under <root>/documents/<first two characters>/<key>."""

    def __init__(self, root: Path, key: bytes):
        super().__init__(key)
        self._root = root

    def _path(self, storage_key: str) -> Path:
        _check_key(storage_key)
        return self._root / "documents" / storage_key[:2] / storage_key

    def put(self, storage_key: str, data: bytes, *, associated_data: bytes) -> None:
        blob = self._seal(data, associated_data)

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
        return self._open(storage_key, blob, associated_data)

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)


class DatabaseFileStore(_Sealer):
    """Files in the stored_files table, encrypted exactly as on disk."""

    def __init__(self, engine: Engine, key: bytes):
        super().__init__(key)
        self._engine = engine

    def put(self, storage_key: str, data: bytes, *, associated_data: bytes) -> None:
        _check_key(storage_key)
        blob = self._seal(data, associated_data)
        statement = insert(StoredFile).values(storage_key=storage_key, blob=blob)
        statement = statement.on_conflict_do_update(
            index_elements=[StoredFile.storage_key], set_={"blob": blob}
        )
        with self._engine.begin() as connection:
            connection.execute(statement)

    def get(self, storage_key: str, *, associated_data: bytes) -> bytes:
        _check_key(storage_key)
        with self._engine.connect() as connection:
            blob = connection.scalar(
                select(StoredFile.blob).where(StoredFile.storage_key == storage_key)
            )
        if blob is None:
            raise FileNotFoundError(storage_key)
        return self._open(storage_key, bytes(blob), associated_data)

    def delete(self, storage_key: str) -> None:
        _check_key(storage_key)
        with self._engine.begin() as connection:
            connection.execute(delete(StoredFile).where(StoredFile.storage_key == storage_key))


@lru_cache
def get_file_store() -> EncryptedFileStore | DatabaseFileStore:
    settings = get_settings()
    key = base64.urlsafe_b64decode(settings.file_encryption_key.get_secret_value())
    if settings.file_store == "database":
        from app.db.session import get_engine

        return DatabaseFileStore(get_engine(), key)
    return EncryptedFileStore(settings.storage_dir, key)
