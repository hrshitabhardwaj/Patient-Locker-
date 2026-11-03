"""Measure query-point extraction on a folder of letters, optionally against an answer key.

Runs the same extraction the application uses (text, OCR, rules, and the model if one is
configured). Prints each letter's points and, with --key, a score per letter and overall.

    python -m scripts.evaluate_letters --letters ~/Downloads/heldout_letters_private
    python -m scripts.evaluate_letters --letters DIR --key DIR/answer_key.json

The key uses the same format as the dev answer key: {"letters": [{"letter": "x.pdf",
"query_points": [{"document_type": ...}, ...]}]}. Nothing is stored or sent anywhere.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from app.ai.llm import get_llm_client
from app.ai.query_extraction import extract_points
from app.processing.extraction import extract_pages
from app.processing.file_validation import UploadRejected, validate_upload
from app.processing.query_segments import segment_letter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--letters", required=True, type=Path, help="folder of PDF/JPEG/PNG letters"
    )
    parser.add_argument("--key", type=Path, help="answer key JSON (optional)")
    args = parser.parse_args()

    files = sorted(
        p
        for p in args.letters.expanduser().iterdir()
        if p.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png")
    )
    if not files:
        print(f"No letters found in {args.letters}")
        return 1
    key = {}
    if args.key:
        key = {
            item["letter"]: item
            for item in json.loads(args.key.expanduser().read_text())["letters"]
        }

    client = get_llm_client()
    print(f"Model: {client.model_name if client else 'none (rules only)'}\n")
    total_expected = total_found = total_type_matches = 0

    for path in files:
        data = path.read_bytes()
        try:
            mime = validate_upload(data).mime_type
        except UploadRejected as exc:
            print(f"== {path.name}: rejected at upload ({exc.message})\n")
            continue
        pages = extract_pages(data, mime)
        segments = segment_letter("\n\n".join(p.text for p in pages))
        points, warnings, method = extract_points(
            [(s.index, s.text) for s in segments if s.used], client
        )

        print(f"== {path.name}  ({len(points)} points, text: {pages[0].method.value}, {method})")
        for n, point in enumerate(points, start=1):
            conditions = ", ".join(c["code"] for c in point.conditions) or "-"
            flag = f"  [review: {point.review_reason}]" if point.needs_review else ""
            kind = point.requirement_type.value
            print(f"   {n}. {kind:21s} {point.requirement[:70]}  ({conditions}){flag}")
        for warning in warnings:
            print(f"   note: {warning}")

        if path.name in key:
            expected = Counter(p["document_type"] for p in key[path.name]["query_points"])
            found = Counter(p.requirement_type.value for p in points)
            matches = sum((expected & found).values())
            total_expected += sum(expected.values())
            total_found += len(points)
            total_type_matches += matches
            wanted = sum(expected.values())
            print(f"   score: {matches}/{wanted} expected points found with the right type")
            missing = expected - found
            extra = found - expected
            if missing:
                print(f"   missing: {dict(missing)}")
            if extra:
                print(f"   extra:   {dict(extra)}")
        print()

    if key:
        print(
            f"OVERALL: {total_type_matches}/{total_expected} expected points found with the right "
            f"type; {total_found} points extracted in total."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
