"""Whether a database's schema is at the latest Alembic migration."""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@lru_cache
def latest_migration() -> str | None:
    return ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()


def current_migration(db: Session) -> str | None:
    """The applied revision, or None for a database that was never migrated."""
    if not db.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
        return None
    return db.scalar(text("SELECT version_num FROM alembic_version"))


def is_up_to_date(db: Session) -> bool:
    return current_migration(db) == latest_migration()
