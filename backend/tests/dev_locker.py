"""Fictional locker documents for the company's dev letters, built to match the answer key's
"locker_evidence" for each point. All names, hospitals and values are invented."""

import io
from dataclasses import dataclass

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@dataclass
class LockerFile:
    filename: str
    category: str
    pdf: bytes


def _pdf(
    lines: list[str], *, signature: str | None = None, stamp: list[str] | None = None
) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    y = 800
    for line in lines:
        page.setFont("Helvetica-Bold" if line.isupper() else "Helvetica", 11)
        page.drawString(50, y, line)
        y -= 18
    if signature:
        # A pen-like squiggle above the doctor's printed name.
        page.setStrokeColor(HexColor("#1a2a6c"))
        page.setLineWidth(1.6)
        path = page.beginPath()
        path.moveTo(60, y - 30)
        path.curveTo(80, y, 95, y - 55, 115, y - 25)
        path.curveTo(130, y - 5, 145, y - 50, 170, y - 22)
        path.curveTo(185, y - 8, 200, y - 40, 230, y - 28)
        page.drawPath(path)
        page.setFillColor(HexColor("#000000"))
        page.setFont("Helvetica", 10)
        page.drawString(60, y - 50, signature)
    if stamp:
        page.setStrokeColor(HexColor("#b0303a"))
        page.setFillColor(HexColor("#b0303a"))
        page.setLineWidth(2)
        page.roundRect(330, y - 75, 200, 70, 8)
        for i, text in enumerate(stamp):
            page.setFont("Helvetica-Bold", 10)
            page.drawCentredString(430, y - 25 - 16 * i, text)
    page.save()
    return buffer.getvalue()


def letter_01_locker() -> list[LockerFile]:
    """Rohan Mehtani, appendicitis, admitted 12/09/2026 to 16/09/2026."""
    return [
        LockerFile(
            "discharge_summary.pdf",
            "discharge_summary",
            _pdf(
                [
                    "GREENFIELD LOTUS HOSPITAL, PUNE",
                    "DISCHARGE SUMMARY",
                    "Patient: Rohan Mehtani",
                    "Admission date: 12/09/2026",
                    "Discharge date: 16/09/2026",
                    "Diagnosis: Acute appendicitis",
                    "Procedure: Laparoscopic appendicectomy on 13/09/2026",
                    "Condition at discharge: Stable",
                    "Treating doctor: Dr. Kavita Rao, MS (General Surgery)",
                ],
                signature="Dr. Kavita Rao",
                stamp=["GREENFIELD LOTUS HOSPITAL", "PUNE", "REG. NO. MH-4471"],
            ),
        ),
        LockerFile(
            "hospital_bill.pdf",
            "hospital_bill",
            _pdf(
                [
                    "GREENFIELD LOTUS HOSPITAL, PUNE",
                    "FINAL BILL - SUMMARY",
                    "Bill No GLH/2026/7781",
                    "Patient: Rohan Mehtani",
                    "Admission 12/09/2026 to 16/09/2026",
                    "Total hospitalisation charges 1,48,500.00",
                    "Amount paid 1,48,500.00",
                ]
            ),
        ),
        LockerFile(
            "usg_abdomen.pdf",
            "investigation_report",
            _pdf(
                [
                    "GREENFIELD LOTUS HOSPITAL - RADIOLOGY",
                    "USG ABDOMEN REPORT",
                    "Patient: Rohan Mehtani",
                    "Date: 12/09/2026",
                    "Indication: Pre-operative evaluation, right iliac fossa pain",
                    "Findings: Non-compressible tubular structure in right iliac fossa.",
                    "Impression: Features suggestive of acute appendicitis.",
                ]
            ),
        ),
    ]


def letter_02_locker() -> list[LockerFile]:
    """Neha Sarangi, dengue, admitted 02/10/2026 to 07/10/2026."""
    pharmacy = []
    for number, (date, items) in enumerate(
        [
            (
                "02/10/2026",
                [
                    ("Paracetamol 650 mg", "96.00"),
                    ("ORS sachets", "120.00"),
                    ("IV fluids", "640.00"),
                ],
            ),
            (
                "04/10/2026",
                [("Pantoprazole 40 mg", "85.00"), ("IV fluids", "640.00"), ("Antiemetic", "70.00")],
            ),
            (
                "06/10/2026",
                [
                    ("Paracetamol 650 mg", "48.00"),
                    ("Multivitamin", "110.00"),
                    ("ORS sachets", "60.00"),
                ],
            ),
        ],
        start=1,
    ):
        pharmacy.append(
            LockerFile(
                f"pharmacy_bill_{number}.pdf",
                "pharmacy_bill",
                _pdf(
                    [
                        "SARASWATI CARE HOSPITAL PHARMACY",
                        "PHARMACY BILL",
                        f"Invoice number PH-2026-55{number}",
                        f"Invoice date {date}",
                        "Patient: Neha Sarangi",
                        *[f"{name} {amount}" for name, amount in items],
                    ]
                ),
            )
        )
    return [
        LockerFile(
            "discharge_summary.pdf",
            "discharge_summary",
            _pdf(
                [
                    "SARASWATI CARE HOSPITAL, LUCKNOW",
                    "DISCHARGE SUMMARY",
                    "Patient: Neha Sarangi",
                    "Admission date: 02/10/2026",
                    "Discharge date: 07/10/2026",
                    "Diagnosis: Dengue fever with thrombocytopenia",
                    "Treated with IV fluids and supportive care.",
                ]
            ),
        ),
        LockerFile(
            "ns1_report.pdf",
            "investigation_report",
            _pdf(
                [
                    "SARASWATI CARE HOSPITAL LABORATORY",
                    "LABORATORY REPORT",
                    "Patient: Neha Sarangi",
                    "Sample collected: 02/10/2026",
                    "Test: Dengue NS1 Antigen (ELISA)",
                    "Result: POSITIVE",
                ]
            ),
        ),
        LockerFile(
            "cbc_report.pdf",
            "investigation_report",
            _pdf(
                [
                    "SARASWATI CARE HOSPITAL LABORATORY",
                    "COMPLETE BLOOD COUNT (CBC)",
                    "Patient: Neha Sarangi",
                    "Sample collected: 02/10/2026",
                    "Haemoglobin 12.1 g/dL",
                    "Total leucocyte count 3,900 /uL",
                    "Platelet count 68,000 /uL",
                ]
            ),
        ),
        *pharmacy,
    ]


def letter_03_locker() -> list[LockerFile]:
    """Arjun Bhatnagar, cataract surgery left eye, day care on 05/10/2026."""
    return [
        LockerFile(
            "discharge_summary.pdf",
            "discharge_summary",
            _pdf(
                [
                    "CLEARSIGHT EYE CENTRE, JAIPUR",
                    "DISCHARGE SUMMARY",
                    "Patient: Arjun Bhatnagar",
                    "Admission date: 05/10/2026",
                    "Discharge date: 05/10/2026",
                    "Diagnosis: Senile cataract, left eye",
                    "Surgery performed: Phacoemulsification with IOL implantation, left eye",
                ]
            ),
        ),
        LockerFile(
            "lens_invoice.pdf",
            "hospital_bill",
            _pdf(
                [
                    "CLEARSIGHT EYE CENTRE, JAIPUR",
                    "INVOICE - INTRAOCULAR LENS",
                    "Invoice number CEC/IOL/2026/318",
                    "Patient: Arjun Bhatnagar",
                    "Lens model: ClearView Aspheric CV-21 (fictional)",
                    "Power: +21.5 D",
                    "Batch no: L2026-5531",
                    "Amount 18,500.00",
                ]
            ),
        ),
        LockerFile(
            "ascan_biometry.pdf",
            "investigation_report",
            _pdf(
                [
                    "CLEARSIGHT EYE CENTRE - DIAGNOSTICS",
                    "A-SCAN BIOMETRY REPORT",
                    "Patient: Arjun Bhatnagar",
                    "Date: 01/10/2026",
                    "Eye: Left eye (OS)",
                    "Axial length 23.41 mm",
                    "Recommended IOL power +21.5 D",
                ]
            ),
        ),
        LockerFile(
            "policy_schedule.pdf",
            "policy_copy",
            _pdf(
                [
                    "KESTRELLINE HEALTH INSURANCE LTD.",
                    "POLICY SCHEDULE",
                    "Policy number KHI-HP-6620147",
                    "Insured: Arjun Bhatnagar",
                    "Policy period: 01/04/2026 to 31/03/2027",
                    "Sum insured 5,00,000.00",
                ]
            ),
        ),
    ]


DEV_PATIENTS = {
    "letter_01.pdf": {
        "full_name": "Rohan Mehtani",
        "claim_number": "KHI/RC/2026/104418",
        "policy_number": "KHI-HP-7719032",
        "locker": letter_01_locker,
        # Documents the vision check would find a signature and stamp on.
        "signed_and_stamped": {"discharge_summary.pdf"},
    },
    "letter_02.pdf": {
        "full_name": "Neha Sarangi",
        "claim_number": "OVG/2026/HL/30981",
        "policy_number": "OVG-FH-448120",
        "locker": letter_02_locker,
        "signed_and_stamped": set(),
    },
    "letter_03.pdf": {
        "full_name": "Arjun Bhatnagar",
        "claim_number": "FT/KHI/2026/44172",
        "policy_number": "KHI-HP-6620147",
        "locker": letter_03_locker,
        "signed_and_stamped": set(),
    },
}
