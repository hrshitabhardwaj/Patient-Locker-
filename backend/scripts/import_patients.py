"""Load patient folders (profile + locker documents) into a running portal through its API.

    python -m scripts.import_patients FOLDER [--api URL] [--user EMAIL] [options]
    (or: make import-patients FOLDER=... API=... USER_EMAIL=...)

FOLDER holds one sub-folder per patient:

    Rohan_Mehtani/
      patient.txt         "Key: value" lines: Full name, Phone, Email, Date of birth,
                          Policy number, Claim number (Full name, Phone and Email required)
      locker/             the patient's documents (PDF, JPEG, PNG). A name such as
                          "discharge_summary__ds.pdf" sets the category; otherwise the
                          system classifies the document itself.
      QUERY_LETTER*.pdf   insurer query letters (uploaded only with --letters, because
                          uploading a letter starts the automatic patient message)

The import goes through the same API as the portal, signed in as a staff member, so access
rules and the audit log apply. It can be run again safely: a patient with the same claim
number (or the same name and phone) is reused, and files already in the locker are skipped.

Use --contact-phone / --contact-email to send every patient's messages to one test phone and
email (for example the evaluators' test contacts).
"""

import argparse
import getpass
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx2

CATEGORIES = {
    "discharge_summary",
    "hospital_bill",
    "investigation_report",
    "consultation_paper",
    "pharmacy_bill",
    "policy_copy",
    "health_card",
    "kyc_document",
    "other",
}
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
PROFILE_KEYS = {
    "full name": "full_name",
    "name": "full_name",
    "phone": "phone",
    "mobile": "phone",
    "email": "email",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "policy number": "policy_number",
    "policy no": "policy_number",
    "claim number": "claim_number",
    "claim no": "claim_number",
}


class ImportError_(Exception):
    pass


def read_profile(path: Path) -> dict[str, str]:
    profile: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition(":")
        field = PROFILE_KEYS.get(re.sub(r"\s+", " ", key.strip().lower().rstrip(".")))
        if sep and field and value.strip():
            profile[field] = value.strip()
    if "date_of_birth" in profile:
        raw = profile["date_of_birth"]
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                profile["date_of_birth"] = datetime.strptime(raw, fmt).date().isoformat()
                break
            except ValueError:
                continue
        else:
            raise ImportError_(f"{path}: date of birth '{raw}' is not a date (use YYYY-MM-DD)")
    missing = [f for f in ("full_name", "phone", "email") if f not in profile]
    if missing:
        raise ImportError_(f"{path}: missing {', '.join(missing)}")
    return profile


def document_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in FILE_TYPES and p.is_file())


def category_of(path: Path) -> str | None:
    prefix, sep, _ = path.name.partition("__")
    return prefix if sep and prefix in CATEGORIES else None


class Portal:
    def __init__(self, api: str):
        self.base = api.rstrip("/") + "/api/v1"
        self.client = httpx2.Client(timeout=120, follow_redirects=False)
        self.csrf = ""

    def _check(self, response: httpx2.Response, what: str) -> dict:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:200]
            raise ImportError_(f"{what}: {response.status_code} {detail}")
        return response.json() if response.content else {}

    def login(self, email: str, password: str) -> None:
        body = self._check(
            self.client.post(
                f"{self.base}/auth/login", json={"email": email, "password": password}
            ),
            "Sign-in",
        )
        self.csrf = body["csrf_token"]
        if body.get("user", {}).get("must_change_password"):
            raise ImportError_("Sign-in: this account must change its password in the portal first")

    def logout(self) -> None:
        self.client.post(f"{self.base}/auth/logout", headers={"X-CSRF-Token": self.csrf})

    def find_patient(self, profile: dict[str, str]) -> dict | None:
        term = profile.get("claim_number") or profile["full_name"]
        items = self._check(
            self.client.get(f"{self.base}/patients", params={"q": term[:100], "limit": 200}),
            "Patient search",
        )["items"]
        for item in items:
            if profile.get("claim_number") and item.get("claim_number") == profile["claim_number"]:
                return item
        digits = re.sub(r"\D", "", profile["phone"])[-10:]
        for item in items:
            same_name = item["full_name"].strip().lower() == profile["full_name"].strip().lower()
            if same_name and re.sub(r"\D", "", item["phone"])[-10:] == digits:
                return item
        return None

    def create_patient(self, profile: dict[str, str]) -> dict:
        return self._check(
            self.client.post(
                f"{self.base}/patients", json=profile, headers={"X-CSRF-Token": self.csrf}
            ),
            "Create patient",
        )

    def update_contacts(self, patient_id: str, changes: dict[str, str]) -> None:
        self._check(
            self.client.patch(
                f"{self.base}/patients/{patient_id}",
                json=changes,
                headers={"X-CSRF-Token": self.csrf},
            ),
            "Update patient",
        )

    def upload(self, url: str, path: Path, category: str | None) -> str:
        """Returns 'uploaded' or 'already there'."""
        with path.open("rb") as handle:
            response = self.client.post(
                url,
                files={"file": (path.name, handle, FILE_TYPES[path.suffix.lower()])},
                data={"category": category} if category else {},
                headers={"X-CSRF-Token": self.csrf},
            )
        if response.status_code == 409:
            return "already there"
        self._check(response, f"Upload {path.name}")
        return "uploaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument(
        "--api",
        default=os.environ.get("PL_API", "http://localhost:8000"),
        help="portal or API address, e.g. https://patient-locker.vercel.app",
    )
    parser.add_argument(
        "--user", default=os.environ.get("PL_USER"), help="staff email to sign in with"
    )
    parser.add_argument(
        "--letters",
        action="store_true",
        help="also upload QUERY_LETTER* files (this sends patient messages)",
    )
    parser.add_argument("--contact-phone", help="use this phone for every patient")
    parser.add_argument("--contact-email", help="use this email for every patient")
    parser.add_argument("--dry-run", action="store_true", help="only check the folders")
    args = parser.parse_args(argv)

    folders = sorted(p for p in args.folder.iterdir() if (p / "patient.txt").is_file())
    if not folders:
        print(f"No patient folders (with patient.txt) found in {args.folder}", file=sys.stderr)
        return 1

    plans = []
    try:
        for folder in folders:
            profile = read_profile(folder / "patient.txt")
            if args.contact_phone:
                profile["phone"] = args.contact_phone
            if args.contact_email:
                profile["email"] = args.contact_email
            locker = document_files(folder / "locker")
            letters = [
                p for p in document_files(folder) if p.name.upper().startswith("QUERY_LETTER")
            ]
            plans.append((folder, profile, locker, letters))
            print(f"{folder.name}: {len(locker)} locker file(s), {len(letters)} letter(s)")
    except ImportError_ as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("Dry run: nothing was sent.")
        return 0

    if not args.user:
        args.user = input("Staff email: ").strip()
    password = os.environ.get("PL_PASSWORD") or getpass.getpass("Password: ")

    portal = Portal(args.api)
    try:
        portal.login(args.user, password)
        for folder, profile, locker, letters in plans:
            existing = portal.find_patient(profile)
            if existing:
                patient = existing
                changes = {
                    key: profile[key]
                    for key in ("phone", "email")
                    if (args.contact_phone and key == "phone")
                    or (args.contact_email and key == "email")
                }
                if changes:
                    portal.update_contacts(patient["id"], changes)
                state = "existing"
            else:
                patient = portal.create_patient(profile)
                state = "created"
            print(f"\n{folder.name} -> {patient['patient_code']} ({state})")
            documents_url = f"{portal.base}/patients/{patient['id']}/documents"
            for path in locker:
                outcome = portal.upload(documents_url, path, category_of(path))
                print(f"  locker  {path.name}: {outcome}")
            if args.letters:
                letters_url = f"{portal.base}/patients/{patient['id']}/query-letters"
                for path in letters:
                    outcome = portal.upload(letters_url, path, None)
                    print(f"  letter  {path.name}: {outcome}")
        portal.logout()
    except ImportError_ as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    except httpx2.TransportError as exc:
        print(f"Stopped: could not reach {args.api} ({type(exc).__name__})", file=sys.stderr)
        return 1
    print("\nDone. Documents are processed in the background; check the portal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
