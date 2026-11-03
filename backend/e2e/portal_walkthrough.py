"""End-to-end check of the portal in real Chrome, the way a staff member uses it.

Flow: sign in -> create patient -> upload locker documents -> upload query letter -> wait for
automatic processing -> check every point's status -> resolve a held point -> confirm the
patient was notified. Screenshots are saved for each step.

Needs the API, worker, portal and Ollama running, the demo files (`make demo-files`), and
an account:   E2E_EMAIL=... E2E_PASSWORD=... make test-e2e
It creates real (fictional) patients in the database it runs against.
"""

import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2] / "demo"
SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "e2e-screenshots")
SHOTS.mkdir(parents=True, exist_ok=True)
EMAIL, PASSWORD = os.environ["E2E_EMAIL"], os.environ["E2E_PASSWORD"]
BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:5173")

KEY = {
    item["letter"]: item
    for item in json.loads(
        (
            Path(__file__).resolve().parents[1] / "tests/fixtures/company_dev/answer_key_dev.json"
        ).read_text()
    )["letters"]
}

CASES = [
    (
        "letter_01_Rohan_Mehtani",
        "Rohan Mehtani",
        "KHI/RC/2026/104418",
        "KHI-HP-7719032",
        "Patient notified",
    ),
    (
        "letter_02_Neha_Sarangi",
        "Neha Sarangi",
        "OVG/2026/HL/30981",
        "OVG-FH-448120",
        "Needs staff review",
    ),
]


def shot(page, name):
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    print(f"  screenshot: {name}.png")


def run_case(page, folder, name, claim, policy, expected_status):
    t0 = time.time()
    print(f"\n== {name}")
    page.get_by_role("link", name="Patients", exact=True).click()
    page.get_by_role("button", name="+ New patient").click()
    page.get_by_label("Full name *").fill(name)
    page.get_by_label("Mobile number *").fill("98123 45678")
    page.get_by_label("Email *").fill(f"{name.split()[0].lower()}@example.com")
    page.get_by_label("Policy number").fill(policy)
    page.get_by_label("Claim number").fill(claim)
    page.get_by_role("button", name="Create patient").click()
    expect(page.get_by_text("Patient created with Patient ID")).to_be_visible(timeout=10000)
    code = page.locator("span.font-mono").first.inner_text()
    print(f"  created patient {code}")

    locker_form = page.locator("form").filter(has_text="Upload a document")
    for path in sorted((ROOT / folder / "locker").glob("*.pdf")):
        category = path.name.split("__")[0]
        locker_form.locator("input[type=file]").set_input_files(str(path))
        locker_form.locator("select").select_option(category)
        locker_form.get_by_role("button", name="Upload", exact=True).click()
        expect(page.get_by_role("link", name=path.name.split("__")[1])).to_be_visible(timeout=10000)
    print(f"  uploaded {len(list((ROOT / folder / 'locker').glob('*.pdf')))} documents")
    expect(page.get_by_text("Processing…")).to_have_count(0, timeout=60000)
    expect(page.get_by_text("Queued", exact=True)).to_have_count(0, timeout=60000)
    print(f"  documents processed ({time.time() - t0:.0f}s)")
    shot(page, f"{folder}_1_locker")

    letter_form = page.locator("form").filter(has_text="Upload the insurer's query letter")
    letter_form.locator("input[type=file]").set_input_files(
        str(next((ROOT / folder).glob("QUERY_LETTER_*.pdf")))
    )
    t1 = time.time()
    letter_form.get_by_role("button", name="Upload", exact=True).click()
    expect(page.get_by_text(expected_status).first).to_be_visible(timeout=240000)
    seconds = time.time() - t1
    print(f"  letter reached '{expected_status}' {seconds:.0f}s after upload, no further clicks")
    shot(page, f"{folder}_2_profile")

    page.get_by_role("button", name="Open").first.click()
    expect(page.get_by_text("Query points (")).to_be_visible()
    statuses = page.locator(
        "li span:text-matches('^(COVERED|NEEDS_FROM_PATIENT|NEEDS_REVIEW)$')"
    ).all_inner_texts()
    expected = [p["expected_status"] for p in KEY[folder[:9] + ".pdf"]["query_points"]]
    print(f"  point statuses: {statuses}")
    assert statuses == expected, f"expected {expected}"
    shot(page, f"{folder}_3_query_details")
    return statuses


def resolve_first_review(page, folder):
    page.get_by_role("button", name="Resolve this point").first.click()
    page.get_by_label("Ask the patient for it").check()
    page.get_by_placeholder("Reason for the decision").fill(
        "Only one CBC on file; ask the patient for the serial counts."
    )
    page.get_by_role("button", name="Save decision").click()
    expect(page.get_by_text("Patient notified").first).to_be_visible(timeout=60000)
    print("  after staff decision: letter sent automatically -> 'Patient notified'")
    shot(page, f"{folder}_4_after_review")


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.goto(BASE_URL)
    page.get_by_label("Email").fill(EMAIL)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    print("signed in")

    for folder, name, claim, policy, expected in CASES:
        run_case(page, folder, name, claim, policy, expected)
        if expected == "Needs staff review":
            resolve_first_review(page, folder)

    page.get_by_role("link", name="Dashboard", exact=True).click()
    expect(page.get_by_text("Recent query letters")).to_be_visible()
    page.wait_for_timeout(1500)
    shot(page, "0_dashboard")
    page.get_by_role("link", name="Patients", exact=True).click()
    page.wait_for_timeout(1000)
    shot(page, "0_patients")
    browser.close()
print("\nE2E OK")
