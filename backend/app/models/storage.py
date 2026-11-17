from datetime import datetime

from sqlalchemy import LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StoredFile(Base):
    """An encrypted document file, used when FILE_STORE=database.

    Only the encrypted bytes are kept here, keyed by the document's storage key; nothing in
    this table identifies a patient.
    """

    __tablename__ = "stored_files"

    storage_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
