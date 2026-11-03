import logging

import pytest

from app.core.logging import RedactingFilter, redact


@pytest.mark.parametrize(
    "raw",
    [
        "contact asha.verma@example.com",
        "phone +91 98765 43210",
        "phone 9876543210",
        "phone +1-415-555-0100",
    ],
)
def test_contact_details_are_redacted(raw):
    cleaned = redact(raw)
    assert "@example.com" not in cleaned
    assert not any(seq in cleaned for seq in ("98765", "9876543210", "555-0100"))


@pytest.mark.parametrize(
    "safe",
    [
        "patient 550e8400-e29b-41d4-a716-446655440000",
        "letter dated 2026-11-02",
        "processed 3 pages in 1200ms",
    ],
)
def test_identifiers_and_dates_are_left_alone(safe):
    assert redact(safe) == safe


def test_filter_redacts_formatted_args():
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "sending to %s", ("asha@example.com",), None
    )
    RedactingFilter().filter(record)
    assert record.getMessage() == "sending to [email]"


def test_query_strings_are_stripped_from_urls():
    line = 'GET http://testserver/api/v1/patients?q=Rohan%20Mehtani&limit=5 "HTTP/1.1 200 OK"'

    cleaned = redact(line)

    assert cleaned == 'GET http://testserver/api/v1/patients?[query] "HTTP/1.1 200 OK"'
