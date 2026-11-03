"""The matching rules, one situation at a time. No database: plain inputs and decisions."""

import uuid
from datetime import date

import pytest

from app.matching.engine import (
    LetterContext,
    LockerDocument,
    PointToMatch,
    decide,
    letter_outcome,
)
from app.models import (
    ClassificationMethod,
    DocumentCategory,
    LetterOutcome,
    MatchStatus,
    ProcessingStatus,
    RequirementType,
)

CONTEXT = LetterContext(
    patient_name="Rohan Mehtani", admission=date(2026, 9, 12), discharge=date(2026, 9, 16)
)
SIGNED = {"code": "signed_by_doctor", "text": "signed by the treating doctor"}
STAMPED = {"code": "stamped", "text": "with hospital stamp or seal"}


def doc(
    text: str,
    category: DocumentCategory | None,
    *,
    name: str = "file.pdf",
    status: ProcessingStatus = ProcessingStatus.PROCESSED,
    profile: dict | None = None,
    lines: int = 0,
    ocr: float | None = None,
    patient: str | None = None,
    detected: DocumentCategory | None = None,
    method: ClassificationMethod | None = None,
) -> LockerDocument:
    return LockerDocument(
        id=uuid.uuid4(),
        filename=name,
        status=status,
        declared_category=category,
        detected_category=detected,
        classification_method=method,
        pages=[(1, text, ocr)],
        fields={"patient_name": {"value": patient}} if patient else {},
        line_item_count=lines,
        profile=profile,
    )


def point(requirement: str, kind: RequirementType, conditions=(), source: str | None = None):
    return PointToMatch(requirement, kind, list(conditions), source or requirement)


def marks(signature: bool = False, stamp: bool = False, checked=(1,), unchecked=()) -> dict:
    return {
        "signatures": [{"page": 1, "source": "vision"}] if signature else [],
        "stamps": [{"page": 1, "label": None, "source": "vision"}] if stamp else [],
        "pages_checked_for_marks": list(checked),
        "pages_unchecked_for_marks": list(unchecked),
    }


DS = "DISCHARGE SUMMARY\nPatient: Rohan Mehtani\nDiagnosis: Acute appendicitis"
SIGNED_DS = point(
    "Discharge summary — signed by the treating doctor; with hospital stamp or seal",
    RequirementType.DISCHARGE_SUMMARY,
    [SIGNED, STAMPED],
)


def test_category_alone_never_proves_a_signature():
    decision = decide(SIGNED_DS, [doc(DS, DocumentCategory.DISCHARGE_SUMMARY)], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_REVIEW
    assert "No AI check" in decision.explanation


def test_signed_and_stamped_summary_is_covered_with_evidence():
    summary = doc(DS, DocumentCategory.DISCHARGE_SUMMARY, profile=marks(True, True))

    decision = decide(SIGNED_DS, [summary], CONTEXT)

    assert decision.status == MatchStatus.COVERED
    kinds = {e.get("kind") for e in decision.evidence}
    assert {"signature", "stamp"} <= kinds
    assert decision.evidence[0]["quote"] == "DISCHARGE SUMMARY"
    assert decision.evidence[0]["verified"]


def test_unsigned_summary_must_come_from_the_patient():
    summary = doc(DS, DocumentCategory.DISCHARGE_SUMMARY, profile=marks(False, True))

    decision = decide(SIGNED_DS, [summary], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_FROM_PATIENT
    assert "No signature found" in decision.explanation


@pytest.mark.parametrize(
    "disclaimer",
    ["Illustrative signature-like mark — not authentic", "Pen mark is not a clinician signature"],
)
def test_mark_the_document_calls_not_genuine_goes_to_review(disclaimer):
    summary = doc(
        DS + "\n" + disclaimer, DocumentCategory.DISCHARGE_SUMMARY, profile=marks(True, True)
    )

    decision = decide(SIGNED_DS, [summary], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_REVIEW
    assert "not genuine" in decision.explanation


def test_page_not_checked_for_marks_is_unknown_not_absent():
    summary = doc(DS, DocumentCategory.DISCHARGE_SUMMARY, profile=marks(checked=(), unchecked=(1,)))

    assert decide(SIGNED_DS, [summary], CONTEXT).status == MatchStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("lines", "expected"),
    [(7, MatchStatus.COVERED), (0, MatchStatus.NEEDS_FROM_PATIENT), (2, MatchStatus.NEEDS_REVIEW)],
)
def test_itemised_bill(lines, expected):
    bill = doc("FINAL HOSPITAL BILL", DocumentCategory.HOSPITAL_BILL, lines=lines)
    requirement = point(
        "Final hospital bill — itemised",
        RequirementType.HOSPITAL_BILL,
        [{"code": "itemised", "text": "itemised"}],
    )

    assert decide(requirement, [bill], CONTEXT).status == expected


def test_report_must_be_about_the_requested_subject():
    usg = point(
        "Ultrasound report",
        RequirementType.INVESTIGATION_REPORT,
        source="Pre-operative ultrasound report of the abdomen.",
    )
    pelvis = doc("USG PELVIS REPORT\nFindings normal", DocumentCategory.INVESTIGATION_REPORT)
    abdomen = doc("USG ABDOMEN REPORT", DocumentCategory.INVESTIGATION_REPORT, name="usg.pdf")

    assert decide(usg, [pelvis], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT
    covered = decide(usg, [pelvis, abdomen], CONTEXT)
    assert covered.status == MatchStatus.COVERED
    assert covered.evidence[0]["filename"] == "usg.pdf"


def test_laterality_must_match():
    biometry = point(
        "Biometry report; A-scan biometry",
        RequirementType.INVESTIGATION_REPORT,
        source="Biometry report A-scan biometry, left eye",
    )
    right_eye = doc(
        "A-SCAN BIOMETRY REPORT\nEye: Right eye (OD)", DocumentCategory.INVESTIGATION_REPORT
    )

    assert decide(biometry, [right_eye], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT


def test_policy_period_must_include_the_admission_date():
    current = point(
        "Policy copy — for the current policy period",
        RequirementType.POLICY_COPY,
        [{"code": "current_period", "text": "for the current policy period"}],
    )
    old = doc(
        "POLICY SCHEDULE\nPolicy period: 01/04/2024 to 31/03/2025", DocumentCategory.POLICY_COPY
    )
    valid = doc(
        "POLICY SCHEDULE\nPolicy period: 01/04/2026 to 31/03/2027", DocumentCategory.POLICY_COPY
    )

    assert decide(current, [old], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT
    assert decide(current, [old, valid], CONTEXT).status == MatchStatus.COVERED


def test_document_with_another_patients_name_is_not_evidence():
    summary = doc(DS, DocumentCategory.DISCHARGE_SUMMARY, patient="Rohan Kapoor")
    requirement = point("Discharge summary", RequirementType.DISCHARGE_SUMMARY)

    decision = decide(requirement, [summary], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_REVIEW
    assert "different patient" in decision.explanation


def test_unreadable_document_could_be_the_missing_one():
    scan = doc("", None, status=ProcessingStatus.UNREADABLE, name="blurry.jpg")
    requirement = point("Discharge summary", RequirementType.DISCHARGE_SUMMARY)

    decision = decide(requirement, [scan], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_REVIEW
    assert "blurry.jpg" in decision.explanation


def test_unrelated_unclassified_document_does_not_cause_review():
    note = doc("Thank you for your visit.", None)
    requirement = point("Discharge summary", RequirementType.DISCHARGE_SUMMARY)

    assert decide(requirement, [note], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT


def test_conflicting_classification_is_not_relied_on():
    unsure = doc(
        DS,
        None,
        detected=DocumentCategory.DISCHARGE_SUMMARY,
        method=ClassificationMethod.CONFLICT,
    )
    requirement = point("Discharge summary", RequirementType.DISCHARGE_SUMMARY)

    assert decide(requirement, [unsure], CONTEXT).status == MatchStatus.NEEDS_REVIEW


def test_poor_scan_missing_the_word_goes_to_review():
    blurry = doc("NS 1 ANT1GEN REP0RT", DocumentCategory.INVESTIGATION_REPORT, ocr=55.0)
    requirement = point("NS1 antigen report", RequirementType.INVESTIGATION_REPORT)

    assert decide(requirement, [blurry], CONTEXT).status == MatchStatus.NEEDS_REVIEW


def test_serial_values_need_more_than_one_date():
    requirement = point(
        "Serial platelet counts — covering the whole period",
        RequirementType.INVESTIGATION_REPORT,
        [{"code": "covers_period", "text": "covering the whole period"}],
    )
    first = doc(
        "CBC\nCollected 12/09/2026\nPlatelet count 68,000", DocumentCategory.INVESTIGATION_REPORT
    )
    second = doc(
        "CBC\nCollected 14/09/2026\nPlatelet count 90,000", DocumentCategory.INVESTIGATION_REPORT
    )

    assert decide(requirement, [first], CONTEXT).status == MatchStatus.NEEDS_REVIEW
    assert decide(requirement, [first, second], CONTEXT).status == MatchStatus.COVERED


def test_original_copy_cannot_be_proven_from_a_scan():
    requirement = point(
        "Discharge summary — original copy",
        RequirementType.DISCHARGE_SUMMARY,
        [{"code": "original", "text": "original copy"}],
    )

    decision = decide(requirement, [doc(DS, DocumentCategory.DISCHARGE_SUMMARY)], CONTEXT)

    assert decision.status == MatchStatus.NEEDS_REVIEW


def test_other_documents_are_found_by_name_in_any_category():
    cheque = point("Cancelled cheque", RequirementType.OTHER)
    in_locker = doc("STATE BANK\nCANCELLED CHEQUE", DocumentCategory.KYC_DOCUMENT)

    assert decide(cheque, [in_locker], CONTEXT).status == MatchStatus.COVERED
    assert decide(cheque, [], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT


def test_clarifications_always_come_from_the_patient():
    requirement = point("Clarify the date of admission", RequirementType.CLARIFICATION)
    summary = doc(DS + "\nAdmission date: 12/09/2026", DocumentCategory.DISCHARGE_SUMMARY)

    assert decide(requirement, [summary], CONTEXT).status == MatchStatus.NEEDS_FROM_PATIENT


def test_point_flagged_at_extraction_stays_in_review():
    flagged = PointToMatch(
        "Attendant undertaking", RequirementType.OTHER, [], "text", True, "found_only_by_model"
    )

    assert decide(flagged, [], CONTEXT).status == MatchStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("statuses", "letter_review", "expected"),
    [
        (
            [MatchStatus.COVERED, MatchStatus.NEEDS_FROM_PATIENT],
            False,
            LetterOutcome.SEND_MISSING_ITEMS_MESSAGE,
        ),
        (
            [MatchStatus.NEEDS_FROM_PATIENT, MatchStatus.NEEDS_REVIEW],
            False,
            LetterOutcome.HOLD_FOR_STAFF_REVIEW,
        ),
        (
            [MatchStatus.COVERED, MatchStatus.COVERED],
            False,
            LetterOutcome.NO_ADDITIONAL_DOCUMENTS_REQUIRED,
        ),
        ([MatchStatus.COVERED], True, LetterOutcome.HOLD_FOR_STAFF_REVIEW),
    ],
)
def test_letter_outcome(statuses, letter_review, expected):
    assert letter_outcome(statuses, letter_review) == expected
