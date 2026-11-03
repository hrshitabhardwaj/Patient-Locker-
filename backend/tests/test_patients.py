import re

from app.core.permissions import Role
from tests.helpers import audit_entries, login

PATIENTS = "/api/v1/patients"
VALID_PATIENT = {
    "full_name": "Asha Verma",
    "phone": "098765 43210",
    "email": "Asha.Verma@Example.com",
    "date_of_birth": "1984-05-17",
    "policy_number": "POL-778812",
    "claim_number": "CLM/2026/0042",
}


def test_staff_creates_patient_and_is_assigned_to_them(client, db, make_user):
    staff = make_user(role=Role.STAFF)
    login(client, staff.email)

    response = client.post(PATIENTS, json=VALID_PATIENT)

    assert response.status_code == 201
    body = response.json()
    assert re.fullmatch(r"PL-\d{6}", body["patient_code"])
    assert body["phone"] == "+919876543210"
    assert body["email"] == "asha.verma@example.com"
    # The creator can open the profile because they were assigned automatically.
    assert client.get(f"{PATIENTS}/{body['id']}").status_code == 200

    [entry] = audit_entries(db, "patient.created")
    assert entry.details == {"patient_code": body["patient_code"]}


def test_patient_ids_are_sequential_and_unique(client, make_user):
    login(client, make_user(role=Role.CASE_MANAGER).email)

    first = client.post(PATIENTS, json=VALID_PATIENT).json()["patient_code"]
    second = client.post(PATIENTS, json=VALID_PATIENT).json()["patient_code"]

    assert int(second[3:]) == int(first[3:]) + 1


def test_invalid_patient_details_are_rejected(client, make_user):
    login(client, make_user().email)

    for field, value in (
        ("phone", "12345"),
        ("email", "not-an-email"),
        ("date_of_birth", "2999-01-01"),
        ("full_name", "   "),
    ):
        response = client.post(PATIENTS, json={**VALID_PATIENT, field: value})
        assert response.status_code == 422, field


def test_staff_only_see_assigned_patients(client, db, make_user, make_patient):
    staff = make_user(role=Role.STAFF)
    mine = make_patient(full_name="Ravi Kumar", assigned_to=(staff,))
    other = make_patient(full_name="Meera Iyer")
    login(client, staff.email)

    listed = client.get(PATIENTS).json()
    assert [p["id"] for p in listed["items"]] == [str(mine.id)]

    # Another patient looks exactly like a missing one, and the attempt is audited.
    assert client.get(f"{PATIENTS}/{other.id}").json() == {"detail": "Patient not found."}
    assert client.get(f"{PATIENTS}/{other.id}/documents").status_code == 404
    denied = audit_entries(db, "access.denied")
    assert {d.patient_id for d in denied} == {other.id}


def test_case_manager_sees_all_patients(client, make_user, make_patient):
    make_patient()
    make_patient()
    login(client, make_user(role=Role.CASE_MANAGER).email)

    assert client.get(PATIENTS).json()["total"] == 2


def test_search_by_name_code_phone_and_claim(client, make_user, make_patient):
    ravi = make_patient(
        full_name="Ravi Kumar", phone_e164="+919812345678", claim_number="CLM-555"
    )
    make_patient(full_name="Meera Iyer", phone_e164="+919000000001")
    login(client, make_user(role=Role.ADMIN).email)

    for q in ("ravi", ravi.patient_code, "98123 45678", "clm-555"):
        items = client.get(PATIENTS, params={"q": q}).json()["items"]
        assert [p["id"] for p in items] == [str(ravi.id)], q


def test_viewing_a_patient_is_audited(client, db, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,))
    login(client, staff.email)

    client.get(f"{PATIENTS}/{patient.id}")

    [entry] = audit_entries(db, "patient.viewed")
    assert entry.patient_id == patient.id and entry.actor_user_id == staff.id


def test_update_records_field_names_but_not_values(client, db, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,), policy_number="OLD-1")
    login(client, staff.email)

    response = client.patch(
        f"{PATIENTS}/{patient.id}", json={"phone": "+91 99887 76655", "policy_number": None}
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+919988776655"
    assert response.json()["policy_number"] is None
    [entry] = audit_entries(db, "patient.updated")
    assert entry.details == {"changed": ["phone_e164", "policy_number"]}


def test_required_fields_cannot_be_cleared(client, make_user, make_patient):
    staff = make_user()
    patient = make_patient(assigned_to=(staff,))
    login(client, staff.email)

    assert client.patch(f"{PATIENTS}/{patient.id}", json={"email": None}).status_code == 422


def test_case_manager_assigns_staff(client, db, make_user, make_patient):
    manager = make_user(role=Role.CASE_MANAGER)
    staff = make_user()
    patient = make_patient()
    login(client, manager.email)

    response = client.put(
        f"{PATIENTS}/{patient.id}/assignments", json={"user_ids": [str(staff.id)]}
    )

    assert response.status_code == 204
    assigned = client.get(f"{PATIENTS}/{patient.id}/assignments").json()
    assert [a["user_id"] for a in assigned] == [str(staff.id)]
    [entry] = audit_entries(db, "patient.assignments_changed")
    assert entry.details == {"added": [str(staff.id)], "removed": []}


def test_staff_cannot_assign_and_inactive_users_cannot_be_assigned(
    client, make_user, make_patient
):
    staff = make_user()
    inactive = make_user(is_active=False)
    patient = make_patient(assigned_to=(staff,))
    login(client, staff.email)
    url = f"{PATIENTS}/{patient.id}/assignments"

    assert client.put(url, json={"user_ids": [str(staff.id)]}).status_code == 403

    login(client, make_user(role=Role.ADMIN).email)
    assert client.put(url, json={"user_ids": [str(inactive.id)]}).status_code == 400


def test_staff_directory_lists_only_names_and_roles(client, make_user):
    login(client, make_user(role=Role.CASE_MANAGER).email)

    entries = client.get("/api/v1/staff-directory").json()

    assert entries and set(entries[0]) == {"id", "full_name", "role"}
