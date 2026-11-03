"""The company's dev letters, end to end: upload locker documents and the letter, let the
pipeline run, and compare every point's status and the letter's outcome with the answer key.

The vision step is simulated here (its real-model check is in test_llm_live.py); everything
else (text extraction, classification, query extraction, matching) runs for real.
"""

import json
from pathlib import Path

import pytest

from app.models import Document, DocumentProfile
from tests.dev_locker import DEV_PATIENTS
from tests.helpers import login, run_jobs

DEV = Path(__file__).parent / "fixtures" / "company_dev"
KEY = {
    letter["letter"]: letter
    for letter in json.loads((DEV / "answer_key_dev.json").read_text())["letters"]
}


def _set_up(client, db, make_user, make_patient, letter_name):
    spec = DEV_PATIENTS[letter_name]
    staff = make_user()
    patient = make_patient(
        assigned_to=(staff,),
        full_name=spec["full_name"],
        claim_number=spec["claim_number"],
        policy_number=spec["policy_number"],
    )
    login(client, staff.email)
    for item in spec["locker"]():
        response = client.post(
            f"/api/v1/patients/{patient.id}/documents",
            files={"file": (item.filename, item.pdf, "application/pdf")},
            data={"category": item.category},
        )
        assert response.status_code == 201, response.text
        if item.filename in spec["signed_and_stamped"]:
            document_id = response.json()["id"]
            db.add(
                DocumentProfile(
                    document_id=document_id,
                    status="succeeded",
                    model="simulated-vision",
                    prompt_version="test",
                    facts={
                        "fields": {},
                        "signatures": [{"page": 1, "source": "vision"}],
                        "stamps": [
                            {
                                "page": 1,
                                "label": "GREENFIELD LOTUS HOSPITAL",
                                "label_verified": True,
                                "source": "vision",
                            }
                        ],
                        "pages_checked_for_marks": [1],
                        "pages_unchecked_for_marks": [],
                    },
                )
            )
            db.commit()
    run_jobs(db)
    assert all(d.processing_status.value == "processed" for d in db.query(Document).all())
    return patient


@pytest.mark.parametrize("letter_name", sorted(KEY))
def test_dev_letter_matches_answer_key(client, db, make_user, make_patient, letter_name):
    patient = _set_up(client, db, make_user, make_patient, letter_name)

    response = client.post(
        f"/api/v1/patients/{patient.id}/query-letters",
        files={"file": (letter_name, (DEV / letter_name).read_bytes(), "application/pdf")},
    )
    letter_id = response.json()["id"]
    run_jobs(db)

    detail = client.get(f"/api/v1/query-letters/{letter_id}").json()
    expected = KEY[letter_name]
    assert detail["identity_status"] == "match"

    got = [(p["requirement_type"], p["current_match"]["status"].upper()) for p in detail["points"]]
    want = [(p["document_type"], p["expected_status"]) for p in expected["query_points"]]
    assert got == want, "\n".join(
        f"{p['seq']}. {p['requirement']}: {p['current_match']['status']} — "
        f"{p['current_match']['explanation']}"
        for p in detail["points"]
    )
    assert detail["outcome"].upper() == expected["expected_outcome"]

    # Every COVERED decision carries evidence quoted from a real document.
    for point in detail["points"]:
        match = point["current_match"]
        if match["status"] == "covered":
            quotes = [e for e in match["evidence"] if "quote" in e]
            assert quotes and all(e["verified"] for e in quotes)
