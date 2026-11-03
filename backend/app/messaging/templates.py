"""The patient message templates, word for word from "Patient Message Templates" (project
templates, not yet the company's official wording). Only the {placeholders} are filled in.

Change the wording here only, and bump TEMPLATE_VERSION, so every stored message records
which wording it used.
"""

TEMPLATE_VERSION = "project-templates-v1"

WHATSAPP_DOCUMENTS_NEEDED = """Dear {patient_name},

We reviewed the query from {insurer_name} for claim {claim_number}. Please share:

{missing_items}

Please send these by {due_date}. Reply here or email {support_email}.

You don't need to resend any other documents.

Regards,
{company_name}"""

EMAIL_DOCUMENTS_NEEDED_SUBJECT = "Action needed: documents for claim {claim_number}"

EMAIL_DOCUMENTS_NEEDED = """Dear {patient_name},

We reviewed the query received from {insurer_name} for claim {claim_number}. Your claim needs the items below before it can move forward.

{missing_items}

Please send these by {due_date}. You can reply to this email or contact us at {support_phone}.

You don't need to resend any other documents.

Regards,
{company_name} Claims Team"""

NOTHING_NEEDED_SUBJECT = "No documents needed for claim {claim_number}"

# Used for both WhatsApp and email.
NOTHING_NEEDED = """Dear {patient_name},

We reviewed the query from {insurer_name} for claim {claim_number}. We already have the required documents, so you don't need to send anything more right now.

We'll contact you if anything else is needed. This message doesn't confirm claim approval.

Regards,
{company_name}"""

# Placeholders each kind of message needs; a missing one blocks the message (usage rule 1).
DOCUMENTS_NEEDED_FIELDS = (
    "patient_name",
    "insurer_name",
    "claim_number",
    "missing_items",
    "due_date",
    "support_email",
    "support_phone",
    "company_name",
)
NOTHING_NEEDED_FIELDS = ("patient_name", "insurer_name", "claim_number", "company_name")
