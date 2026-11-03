"""Write the fictional demo patients (locker documents and query letters) to demo/.

    python -m scripts.make_demo_files      (or `make demo-files`)

Everything is invented; the folder is git-ignored.
"""

import shutil
from pathlib import Path

from app.core.config import ROOT_DIR
from tests.dev_locker import DEV_PATIENTS

DEV_LETTERS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "company_dev"


def main() -> None:
    root = ROOT_DIR / "demo"
    for letter, spec in sorted(DEV_PATIENTS.items()):
        folder = root / f"{letter[:9]}_{spec['full_name'].replace(' ', '_')}"
        (folder / "locker").mkdir(parents=True, exist_ok=True)
        for item in spec["locker"]():
            (folder / "locker" / f"{item.category}__{item.filename}").write_bytes(item.pdf)
        shutil.copy(DEV_LETTERS / letter, folder / f"QUERY_LETTER_{letter}")
        (folder / "patient.txt").write_text(
            f"Full name: {spec['full_name']}\nPhone: 9812345678\nEmail: patient@example.com\n"
            f"Claim number: {spec['claim_number']}\nPolicy number: {spec['policy_number']}\n"
        )
        print(f"wrote {folder.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
