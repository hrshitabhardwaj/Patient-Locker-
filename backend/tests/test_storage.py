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


# ---- Database backend (FILE_STORE=database) ----

from sqlalchemy import delete, select  # noqa: E402

from app.models.storage import StoredFile  # noqa: E402
from app.services.storage import DatabaseFileStore  # noqa: E402


@pytest.fixture
def db_store(migrated_engine):
    store = DatabaseFileStore(migrated_engine, os.urandom(32))
    yield store
    with migrated_engine.begin() as connection:
        connection.execute(delete(StoredFile).where(StoredFile.storage_key.like("dbtest%")))


def test_database_round_trip_and_overwrite(db_store):
    db_store.put("dbtest1", b"patient file", associated_data=b"doc-1")
    db_store.put("dbtest1", b"replaced", associated_data=b"doc-1")

    assert db_store.get("dbtest1", associated_data=b"doc-1") == b"replaced"


def test_database_holds_only_ciphertext(db_store, migrated_engine):
    db_store.put("dbtest2", b"Rohan Mehtani discharge summary", associated_data=b"doc-1")

    with migrated_engine.connect() as connection:
        blob = connection.scalar(select(StoredFile.blob).where(StoredFile.storage_key == "dbtest2"))
    assert b"Rohan" not in bytes(blob)


def test_database_file_moved_to_another_record_fails(db_store):
    db_store.put("dbtest3", b"patient file", associated_data=b"doc-1")

    with pytest.raises(StorageIntegrityError):
        db_store.get("dbtest3", associated_data=b"doc-2")


def test_database_delete_and_missing(db_store):
    db_store.put("dbtest4", b"x", associated_data=b"")
    db_store.delete("dbtest4")

    with pytest.raises(FileNotFoundError):
        db_store.get("dbtest4", associated_data=b"")


def test_database_rejects_unsafe_keys(db_store):
    with pytest.raises(ValueError):
        db_store.put("dbtest'; drop", b"x", associated_data=b"")
