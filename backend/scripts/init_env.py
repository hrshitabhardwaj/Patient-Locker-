"""Create the project-root .env from .env.example with freshly generated secrets.

Refuses to overwrite an existing .env. Uses only the standard library so it can run
before the virtualenv exists.
"""

import base64
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def main() -> int:
    if TARGET.exists():
        print(".env already exists; leaving it unchanged.")
        return 0

    db_password = secrets.token_urlsafe(24)
    generated = {
        "FILE_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    }

    lines = []
    for line in EXAMPLE.read_text().splitlines():
        key = line.split("=", 1)[0]
        if key in generated:
            line = f"{key}={generated[key]}"
        elif key in ("DATABASE_URL", "TEST_DATABASE_URL"):
            line = line.replace("CHANGE_ME", db_password)
        lines.append(line)

    TARGET.write_text("\n".join(lines) + "\n")
    TARGET.chmod(0o600)
    print("Created .env with generated secrets (permissions 600).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
