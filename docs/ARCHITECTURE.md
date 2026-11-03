# Architecture

Approved in Phase 1 (2 Nov 2026). This document records the design and the decisions
behind it; it is updated as each phase lands.

## Guiding rule

The system must never report a requirement as covered without evidence. When it is not
sure, it marks the point `NEEDS_REVIEW` and holds the message for staff. A wrong
`COVERED` is the costliest error: the claim stalls and the patient is never asked.

## Components

```
React portal ──► FastAPI (API) ──► PostgreSQL (data, job queue, audit log)
                      │                  ▲
                      ▼                  │ SELECT … FOR UPDATE SKIP LOCKED
               Private file store    Worker process (pipeline)
               (encrypted at rest)      │
                                        ├─► LLM (switchable: Ollama / Claude)
                                        └─► WhatsApp (Twilio / Meta) · Email (Resend)
                                                 │ delivery webhooks
                                                 └──► FastAPI /webhooks
```

- **Automatic trigger.** An upload writes the document row and its processing job in one
  database transaction, so a successful upload is guaranteed to be processed.
- **Job queue in PostgreSQL** instead of Celery/Redis: one fewer service, jobs survive
  restarts, unique keys stop the same job running twice.
- **Heavy work at upload time.** Supporting documents are analysed once when uploaded;
  a query letter then only needs extraction and matching.

## Pipeline

**Supporting document:** validate file → extract text (PyMuPDF) → OCR pages without a
text layer (Tesseract) → LLM document profile (category, key fields, signatures and
stamps with page numbers) → store. Unreadable files are flagged, never used as evidence.

**Query letter:** extract text → LLM extracts query points (each with an exact quote) →
code checks every quote exists and every request line is covered → LLM matches each
point against the locker → code validates each decision → hold or send.

### Code checks on AI output

- `COVERED` needs a processed document belonging to this patient, and every evidence
  quote must appear in that document's text. Visual facts (signature, stamp) must point
  to a page whose stored profile records that fact. Otherwise → `NEEDS_REVIEW`.
- Any condition the model marks "unknown" → `NEEDS_REVIEW`.
- Best candidate unreadable or low quality → `NEEDS_REVIEW` (not `NEEDS_FROM_PATIENT`).
- Patient name or claim number mismatch → `NEEDS_REVIEW`; nothing is sent.
- Document text is treated as data, never as instructions.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Dev environment | Local Homebrew (PostgreSQL 16, Tesseract), no Docker | User choice |
| Python version | 3.12 | Broadest wheel support for PDF/OCR libraries |
| LLM | Switchable interface: Ollama (local) for development, Claude (hosted) via company key later | No company accounts yet; development machine is 8 GB RAM, so local models are small and less accurate |
| Mixed results | If any point is `NEEDS_REVIEW`, hold the letter's whole message; once staff resolve it, the message sends automatically | Brief forbids both manual send buttons and sending uncertain results |
| Clarification requests | Always `NEEDS_FROM_PATIENT` | Insurers ask because the record is unclear; a written clarification is still needed |
| Signature and stamp checks | Vision analysis of page images | Text extraction cannot show a signature exists |
| Messaging providers | Built against fake/test providers; live Twilio or Meta and Resend accounts added in Phase 12 | No company accounts yet; personal details may not be used |
| Message format | Fixed template rendered by code; LLM only phrases each missing item | Guarantees template adherence |
| Request logging | Path only, no query strings; emails and phone numbers redacted | Search queries can contain patient names |
| Sessions | Server-side sessions in PostgreSQL (httpOnly, SameSite=Strict cookie; token stored hashed) instead of JWT | Logout, deactivation and password reset take effect immediately; a JWT stays valid until it expires |
| CSRF | Per-session token in a readable cookie, echoed in `X-CSRF-Token` on every non-GET request | Defence in depth alongside SameSite=Strict |
| Session lifetime | 30 min idle, 12 h absolute | Shared workstations in clinical offices |
| Login lockout | 5 failures lock the account for 15 min; every failure gets the same message | Brute-force protection without revealing which accounts exist |
| New accounts | Admin-created only, with a server-generated temporary password that must be changed at first sign-in | No public sign-up; admins never choose staff passwords |
| Roles | `admin`, `case_manager`, `staff`; permissions mapped in `app/core/permissions.py` | Staff see only assigned patients (enforced from Phase 4) |
| Audit log | Append-only, enforced by a database trigger; details hold IDs and codes only, never personal data; reading the log is itself audited | Tamper resistance; privacy marks |
| Route protection | Every route requires sign-in unless explicitly listed as public; a test checks every route | A new endpoint that forgets authentication fails the test suite |
| PDF library | pypdfium2 (Apache/BSD) instead of PyMuPDF | PyMuPDF is AGPL; a hosted company product would have to publish its source or buy a licence. PDFium is not thread-safe, so all calls share one lock |
| Patient ID | `PL-000123` from a PostgreSQL sequence | Readable, unique, never reused |
| Patient fields | Name, phone (stored as E.164), email required; date of birth, policy number, claim number optional | Policy and claim numbers let a query letter be checked against the right patient |
| Phone region | Numbers without a country code are read as `DEFAULT_PHONE_REGION` (default `IN`) | WhatsApp needs international format |
| Patient access | Admin and case manager see all; staff see assigned patients and those they create; others get 404 and the attempt is audited | Privacy marks; does not reveal which patients exist |
| File validation | Type from content (magic bytes), then fully opened: PDFs via PDFium (encrypted, empty, damaged, too many pages refused); images fully decoded with a pixel limit | Disguised and malicious files never reach storage or OCR |
| Upload limits | 20 MB / 100 pages / 40 MP, configurable; requests over the limit refused before the body is read | Memory and disk protection |
| Encryption at rest | AES-256-GCM per file, document ID as associated data, files named by ID only, owner-only permissions | A copied or swapped file can't be read or passed off as another record |
| Duplicates | Same SHA-256 for the same patient refused | Stops accidental double uploads skewing matching |
| History | Replacement creates a new version linked to the old; delete is soft; views and downloads audited | "Maintain a document history" |
| Delete permission | Admin and case manager only | Removing evidence changes matching results |
| Serving files | `Cache-Control: no-store`, `nosniff`; images under a sandbox CSP | Nothing cached; nothing in a file can run |
| Background work | Job table in PostgreSQL, claimed with `FOR UPDATE SKIP LOCKED`; queued in the same transaction as the upload; retries with backoff (30 s → 30 min); abandoned jobs reclaimed after a 20-minute lease; dedupe keys | An upload can never be saved without its processing; no Redis to run |
| When to OCR | A page is OCR'd if its text layer has under 200 letters/digits **or** images cover over 30% of it | Sample 04 is a scan with a 29-character text overlay: a "has text?" check alone would skip it |
| Classification | Rules first (title line weighted 5×, body phrases 1×), then the model; agreement raises confidence, disagreement is marked `conflict` and keeps a strong rule result | Rules are instant and work offline; on the samples the model mislabelled the hospital bill while the rules did not |
| Labelled fields | Read by rules (patient, IDs, dates, invoice number, totals, line-item count), never by the model | Exact, instant and needs no verification |
| AI document profile | Two narrow questions: document type and printed names (page 1), then signature/stamp yes-or-no per page (up to 3 pages). Text values checked against the extracted text; unusable mark answers recorded as "unchecked", never "none" | Small local models answer narrow questions far more reliably; unknown must never read as absent |
| Local model | `qwen3-vl:2b-instruct` via Ollama (1.9 GB) | Fits an 8 GB machine; the default `qwen3-vl:2b` tag is a "thinking" variant that ignores requests not to think and runs out of output budget |
| Empty settings | Empty values in `.env` fall back to defaults | A blank `OLLAMA_MODEL=` line silently broke every model call |
| Query extraction | Rules lead, model is a second opinion asked per sentence/list item; named documents from rules are never overridden; model-only items kept and flagged; ungrounded model wording ignored | Small local model labelled most items "other" until given a type guide; even then rules were more accurate. Recall is protected because every candidate yields at least one point |
| Candidate selection | Body between greeting and sign-off; list items always considered; sentences considered if they name a document, ask a question or contain a request verb; lead-ins ("…the following:") and general instructions skipped with a stored reason | Staff can audit exactly what was read and why |
| Conditions | Attached to the document they qualify: words before a document belong to it, words between two documents to the first, trailing words to all when the documents are a plain list | "itemised pharmacy bill and the policy copy" must not make the policy "itemised" |
| Same-type mentions | Merged into one point per clause | "investigation reports including blood tests and X-ray reports" is one requirement |
| Letter identity | Claim number, policy number and name compared; any mismatch flags the letter; only match/mismatch is stored, not the values | Never message a patient about someone else's claim |
| Letter-wide notes | "All documents should be attested" kept as general conditions on the letter | Found by the held-out letter; must reach the patient message |
| Company dev set | Letters 01–03 and their answer key are test fixtures; the private held-out letters are never opened, and the company runs them with `make eval-letters` | Keeps the held-out measurement honest |
| Requirement vocabulary | Follows the company key: indoor case papers = other; lens/implant invoices = hospital bill; any named "… report" = investigation; "X including A and B" = separate points A and B; restated items merged | Baseline on the dev letters was 10/15 points; 15/15 after these general rules |
| Matching | Rules decide; category alone never proves a requirement; COVERED needs every condition met with evidence; NEEDS_FROM_PATIENT only when sure; everything uncertain is NEEDS_REVIEW | Wrong COVERED is the costliest error |
| Subject words | Specific words in the requirement ("ultrasound", "abdomen", "left") must appear in the document, with synonyms (USG, OS) | A USG pelvis report does not answer a request for an abdominal ultrasound |
| Mark disclaimers | A signature or stamp on a page that says "not authentic", "illustrative", "training stamp" is unknown, not met | The company's sample documents contain exactly these |
| AI double-check of COVERED | Built, off by default (`MATCH_MODEL_CHECK`) | On the dev set the local 2B model rejected 2 of 9 correct matches with self-contradicting reasons, wrongly holding 2 of 3 letters; re-measure with a hosted model |
| Matching waits | Matching is deferred (every 15 s, up to ~20 min) while the patient's documents are processing | Decisions are never made on half-read evidence |
| Staff review | Point decisions (covered with a chosen document / needs from patient / not a requirement) and letter confirmations; outcome recalculated; staff decisions survive re-matching; all audited | "No manual send button": staff resolve doubts, sending then follows automatically |
| Message templates | "Patient Message Templates" (project templates, not yet official wording) stored verbatim with a version; values inserted in one pass so braces in values are never re-read; HTML version escapes values | Template adherence (10 marks) |
| All covered | The *nothing needed* template is sent | Product decision |
| Item wording | Built by rules from the stored point (label from the requirement and its conditions; reason from the letter's own qualifier, e.g. "showing the breakup of …", else from the conditions). No model involved | Exact, repeatable, cannot invent; reproduces the template's example line exactly |
| Medical details (rule 4) | Item wording must use only an allowlist of administrative words; otherwise a generic label pointing to the insurer's letter | A blocklist of medical terms can never be complete; an allowlist fails safe |
| Due date | Letter's deadline ("within N days" of the letter date); if none or already passed, today + `PATIENT_RESPONSE_DAYS` (default 7; 0 sends such letters to staff); the source is stored with the message | Most sample letters set no deadline; sending all of them to staff would stall automation |
| Missing values (rule 1) | Message stored as blocked with the missing fields; letter to staff; staff correct the letter's insurer name, claim reference or deadline, or the company settings | Never send a message with a gap or a guessed value |
| Message versions | A new version only when the content changes (content hash); older versions superseded, kept | Idempotent preparation; full history for audit; duplicate prevention in Phase 9 builds on the hash |
| Sending | Queued in the same transaction as the ready message; one notification per (letter, channel, content) with a unique key; marked SENDING before the provider call, so an interrupted send is flagged for staff, never repeated; 429/5xx/network retried, other 4xx failed and sent to staff | No send button, no duplicates, no silent failures |
| Providers | Test outbox (default), Twilio WhatsApp, Meta WhatsApp Cloud API, Resend; real adapters tested against simulated responses only; missing credentials fail loudly | No company accounts yet; live delivery is Phase 12 |
| Delivery webhooks | Signature checked for every request (Twilio HMAC-SHA1 of URL+params, Meta HMAC-SHA256 of body, Resend/Svix HMAC with 5-minute freshness); rejections audited; statuses never move backwards; a failure after sending sends the letter to staff | Public endpoints must not be forgeable |
| Sign-in throttling | Per account (5 failures, 15 min) and per network address (20 failures, 15 min, counted from the audit log) | Stops both guessing one account and spraying many |
| Log redaction | Emails, phone numbers and URL query strings redacted in every log line; HTTP client libraries quietened | The access-sweep test found search terms in a library's request log |
| Front-end routing | React Router 7.18.5 | 6.x carried two moderate advisories (open redirect, SSR); fixed only in 7.18.5 |

## Open items

- Official message template (needed before Phase 8)
- Sample patient profiles, documents and query letters (needed before Phase 5)
- All points covered: send a "nothing further needed" message, or only record the outcome?
- Hosting choice and company WhatsApp/email accounts (Phase 12)
