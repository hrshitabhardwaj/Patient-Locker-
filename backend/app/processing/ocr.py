"""Optical character recognition with Tesseract."""

from collections import defaultdict

import pytesseract
from PIL import Image

from app.core.config import get_settings


def ocr_image(image: Image.Image) -> tuple[str, float | None]:
    """Return the recognised text (line by line) and the mean word confidence (0-100)."""
    data = pytesseract.image_to_data(
        image, lang=get_settings().ocr_languages, output_type=pytesseract.Output.DICT
    )
    lines: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    confidences = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        confidence = float(data["conf"][i])
        if not word or confidence < 0:
            continue
        lines[(data["block_num"][i], data["par_num"][i], data["line_num"][i])].append(word)
        confidences.append(confidence)

    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return text, mean
