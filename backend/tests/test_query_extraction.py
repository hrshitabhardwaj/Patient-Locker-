"""Query point extraction, measured against hand-checked answers for the fictional letters.

Letters 01-05 were used while writing the rules. Letter 06 was written separately and held
back: the rules were not changed after running it.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.ai.llm import LLMResponseInvalid, LLMUnavailable
from app.ai.query_extraction import LLMQueryItem, extract_points, reconcile
from app.models import RequirementType
from app.processing.query_rules import extract_rule_points
from app.processing.query_segments import general_conditions, segment_letter
from tests.helpers import FakeLLM

LETTERS = Path(__file__).parent / "fixtures" / "query_letters"
EXPECTED = {
    k: v for k, v in json.loads((LETTERS / "expected.json").read_text()).items() if k[0] != "_"
}
TUNING = sorted(name for name in EXPECTED if not name.startswith("06"))
HOLDOUT = "06_holdout_bullets.txt"


def _rules_only(name: str):
    segments = segment_letter((LETTERS / name).read_text())
    points, _, _ = extract_points([(s.index, s.text) for s in segments if s.used], None)
    return segments, points


def _summary(points) -> Counter:
    return Counter(
        (p.requirement_type.value, tuple(sorted(c["code"] for c in p.conditions))) for p in points
    )


@pytest.mark.parametrize("name", TUNING)
def test_tuning_letters_match_the_hand_checked_answers(name):
    _, points = _rules_only(name)

    expected = Counter(
        (p["type"], tuple(sorted(p["conditions"]))) for p in EXPECTED[name]["points"]
    )
    assert _summary(points) == expected
    assert not any(p.needs_review for p in points)


def test_held_out_letter_finds_every_requirement():
    segments, points = _rules_only(HOLDOUT)

    # Recall: one point per requirement, none missed, none invented.
    assert len(points) == len(EXPECTED[HOLDOUT]["points"])
    types = Counter(p.requirement_type.value for p in points)
    expected_types = Counter(p["type"] for p in EXPECTED[HOLDOUT]["points"])
    # Recorded result: "Previous treatment records" is read as a consultation paper where the
    # hand-checked answer says "other". 4 of 5 types agree; the rules were not tuned to this.
    assert sum((types & expected_types).values()) == 4
    # The letter-wide "attested" note is kept, not lost.
    [note] = general_conditions(segments)
    assert note["conditions"][0]["code"] == "attested"


def test_every_used_segment_yields_at_least_one_point():
    for name in EXPECTED:
        segments, points = _rules_only(name)
        used = {s.index for s in segments if s.used}
        assert used == {p.segment_index for p in points}, name


def test_greetings_headers_and_deadlines_are_not_requirements():
    segments = segment_letter((LETTERS / "01_numbered_brief_example.txt").read_text())

    skipped = {s.text: s.reason for s in segments if not s.used}
    assert any("15 days" in text for text in skipped)
    assert all("Claim No" not in s.text and "Dear" not in s.text for s in segments)


def test_document_named_only_as_a_reference_is_not_requested():
    points = extract_rule_points(
        "Please clarify the admission date mentioned in the discharge summary."
    )

    assert [p.requirement_type for p in points] == [RequirementType.CLARIFICATION]


def test_unrecognised_request_is_kept_for_review():
    points, _, _ = extract_points([(0, "Kindly submit the attendant's undertaking.")], None)

    [point] = points
    assert point.requirement_type == RequirementType.OTHER
    assert point.needs_review and point.review_reason == "unrecognised_requirement"


# ------------------------------------------------------------- model second opinion


def _item(kind: str, requirement: str, conditions=()) -> LLMQueryItem:
    return LLMQueryItem(requirement_type=kind, requirement=requirement, conditions=list(conditions))


SEGMENT = "Provide an itemised pharmacy bill and a copy of the policy."


def test_agreement_marks_points_as_confirmed_by_both():
    result = reconcile(
        SEGMENT,
        0,
        extract_rule_points(SEGMENT),
        [
            _item("pharmacy_bill", "itemised pharmacy bill"),
            _item("policy_copy", "copy of the policy"),
        ],
    )

    assert [p.source for p in result.points] == ["rules_and_model", "rules_and_model"]
    assert not any(p.needs_review for p in result.points)


def test_item_found_only_by_the_model_is_kept_and_flagged():
    segment = "Provide the discharge summary along with the attendant undertaking form."
    result = reconcile(
        segment,
        0,
        extract_rule_points(segment),
        [
            _item("discharge_summary", "discharge summary"),
            _item("other", "attendant undertaking form"),
        ],
    )

    extra = [p for p in result.points if p.source == "model"]
    assert len(extra) == 1
    assert extra[0].needs_review and extra[0].review_reason == "found_only_by_model"


def test_model_items_not_in_the_text_are_ignored():
    result = reconcile(
        SEGMENT,
        0,
        extract_rule_points(SEGMENT),
        [_item("kyc_document", "aadhaar card of the nominee")],
    )

    assert [p.source for p in result.points] == ["rules", "rules"]
    assert "not in the text" in result.notes[0]


def test_model_disagreeing_on_type_does_not_override_a_named_document():
    segment = "Submit the final hospital bill."
    result = reconcile(
        segment, 0, extract_rule_points(segment), [_item("pharmacy_bill", "final hospital bill")]
    )

    [point] = result.points
    assert point.requirement_type == RequirementType.HOSPITAL_BILL
    assert not point.needs_review
    assert "rules kept" in result.notes[0]


def test_model_can_name_what_the_rules_could_not_but_staff_confirm_it():
    segment = "Kindly submit the attendant's undertaking."
    result = reconcile(
        segment, 0, extract_rule_points(segment), [_item("other", "attendant's undertaking")]
    )

    [point] = result.points
    assert point.source == "model"
    assert point.review_reason == "type_from_model_only"


def test_unavailable_model_falls_back_to_rules_for_the_rest_of_the_letter():
    fake = FakeLLM(error=LLMUnavailable("down"))

    points, warnings, method = extract_points([(0, SEGMENT), (1, "Submit the policy copy.")], fake)

    assert method == "rules"
    assert len(fake.calls) == 1  # not retried for every segment
    assert "model unavailable" in warnings[0]
    assert [p.source for p in points] == ["rules", "rules", "rules"]


def test_unusable_model_answer_for_one_segment_keeps_the_rules():
    fake = FakeLLM({"LLMQueryItems": [LLMResponseInvalid("bad"), {"items": []}]})

    points, warnings, method = extract_points([(0, SEGMENT), (1, "Submit the policy copy.")], fake)

    assert method == "rules_and_model"
    assert "segment 0: model answer unusable" in warnings[0]
    assert len(points) == 3
