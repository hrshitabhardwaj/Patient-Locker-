import os

import pytest

from app.services.storage import EncryptedFileStore, StorageIntegrityError


@pytest.fixture
def store(tmp_path):
    return EncryptedFileStore(tmp_path, os.urandom(32))


def test_round_trip(store):
    store.put("abc123", b"patient file", associated_data=b"doc-1")

    assert store.get("abc123", associated_data=b"doc-1") == b"patient file"


def test_file_moved_to_another_record_fails(store):
    store.put("abc123", b"patient file", associated_data=b"doc-1")

    with pytest.raises(StorageIntegrityError):
        store.get("abc123", associated_data=b"doc-2")


def test_tampered_file_fails(store, tmp_path):
    store.put("abc123", b"patient file", associated_data=b"doc-1")
    [path] = tmp_path.rglob("abc123")
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01
    path.write_bytes(bytes(blob))

    with pytest.raises(StorageIntegrityError):
        store.get("abc123", associated_data=b"doc-1")


def test_wrong_key_fails(store, tmp_path):
    store.put("abc123", b"patient file", associated_data=b"doc-1")

    with pytest.raises(StorageIntegrityError):
        EncryptedFileStore(tmp_path, os.urandom(32)).get("abc123", associated_data=b"doc-1")


def test_storage_keys_cannot_escape_the_folder(store):
    with pytest.raises(ValueError):
        store.put("../evil", b"x", associated_data=b"")


def test_files_are_private_to_the_owner(store, tmp_path):
    store.put("abc123", b"x", associated_data=b"")
    [path] = tmp_path.rglob("abc123")

    assert oct(path.stat().st_mode & 0o777) == "0o600"
