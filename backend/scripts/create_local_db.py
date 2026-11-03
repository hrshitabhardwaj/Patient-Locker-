"""Create the application role and databases on a local PostgreSQL server. Idempotent.

Connects as the current OS user (the Homebrew superuser) to the `postgres` database,
then creates the role and the databases named in DATABASE_URL and TEST_DATABASE_URL.
"""

import sys

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core.config import ROOT_DIR


def main() -> int:
    env = dotenv_values(ROOT_DIR / ".env")
    urls = [make_url(env[key]) for key in ("DATABASE_URL", "TEST_DATABASE_URL") if env.get(key)]
    if not urls:
        print("DATABASE_URL is not set in .env; run `make env` first.")
        return 1

    role, password = urls[0].username, urls[0].password
    if any(url.username != role for url in urls):
        print("DATABASE_URL and TEST_DATABASE_URL must use the same role.")
        return 1

    with psycopg.connect(
        host=urls[0].host or "localhost",
        port=urls[0].port or 5432,
        dbname="postgres",
        autocommit=True,
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        print(f"Role {role}: {'password updated' if exists else 'created'}")

        for url in urls:
            name = url.database
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                print(f"Database {name}: already exists")
                continue
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(role)
                )
            )
            print(f"Database {name}: created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
