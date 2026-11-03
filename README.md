# Patient Locker and Auto Query Response

A secure document locker for insurance-claim patients. When an insurer's query letter is
uploaded, the system extracts every requirement, checks each one against the documents
already in the patient's locker, and automatically messages the patient (WhatsApp and
email) about only what is genuinely missing. Uncertain results are held for staff review
instead of being sent.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and the decisions behind it.

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Requirements and architecture | Approved |
| 2 | Project setup | **Done** — API, database, migrations, portal shell, tests |
| 3 | Database and authentication | **Done** — staff login, roles, sessions, append-only audit log |
| 4 | Patients and document locker | **Done** — patient IDs, search, per-patient access, validated encrypted uploads, history |
| 5 | Document processing (PDF text, OCR, classification) | **Done** — background worker, OCR, rule classification and fields, verified AI profile |
| 6 | Query extraction | **Done** — 15/15 points on the company dev letters; held-out letters to be run by the company |
| 7 | Document matching | **Done** — 15/15 statuses and 3/3 outcomes on the dev letters, end to end with the real model |
| 8 | Message generation | **Done** — project templates word for word; usage rules 1–4 enforced |
| 9 | WhatsApp and email | **Built** — automatic, once per channel, retries, delivery webhooks; Twilio, Meta and Resend adapters tested against simulated responses. **Live delivery untested until Phase 12** |
| 10 | Dashboard and frontend | **Done** — dashboard, patients, locker, query details, review, messages and delivery, staff accounts, audit log |
| 11 | Security, testing and errors | **Done** — access sweep over every route, log-leak test, per-address sign-in limit, browser walkthrough, dependency audit clean |
| 12 | Deployment, live credentials, evaluation prep | Not started |

Live WhatsApp and email delivery is **untested** until company accounts are added in Phase 12.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · Tesseract OCR ·
React 19 + Vite + TypeScript + Tailwind · LLM behind a switchable interface
(local Ollama for development, hosted Claude once a company key is available).

## Local setup (macOS, Homebrew)

Prerequisites: Homebrew and Node 20+.

```bash
brew install python@3.12 postgresql@16 tesseract ollama
make setup
```

`make setup` runs these steps, each of which can also be run alone:

| Target | What it does |
|---|---|
| `make env` | Creates `.env` from `.env.example` with generated secrets and DB password (never overwrites) |
| `make backend-install` | Creates `backend/.venv` and installs the API with dev tools |
| `make frontend-install` | Installs portal dependencies |
| `make db-start` | Starts PostgreSQL as a Homebrew background service |
| `make db-create` | Creates the `patient_locker` role and the app and test databases (idempotent) |
| `make migrate` | Applies database migrations |
| `make llm-setup` | Starts Ollama and downloads the local vision model (about 2 GB) |

## First admin account

There is no public sign-up and no default password. The first administrator is created
from the command line with `scripts/create_admin.py`, which:

- connects only to the database you name: `DATABASE_URL` from the environment, or `--local`
  for the one in `.env` (it never falls back to `.env` on its own);
- shows which database it is about to change and asks you to confirm;
- checks the database is reachable and fully migrated before asking for anything;
- refuses if an active administrator already exists, or if the email is already in use;
- asks for the password twice without showing it (at least 12 characters, letters mixed
  with digits or symbols, not containing the email name) and stores only its Argon2 hash.

**Local database:**

```bash
make create-admin EMAIL=you@clinic.com NAME="Your Name"
```

**Production (Render), from your own machine:**

1. Deploy the API first and check https://patient-locker-backend.onrender.com/api/v1/health
   shows `{"status":"ok","database":"ok"}` (the start command creates the tables).
2. In Render, open the PostgreSQL database → **Connect** → copy the **External Database
   URL**. It contains the database password: don't paste it into chats, files or commits.
   External connections are encrypted (SSL). Under the database's **Access Control** you
   can limit which IP addresses may connect.
3. In a terminal (zsh, the macOS default), paste the URL at a hidden prompt so it stays
   out of your shell history, then run the command:

   ```bash
   cd backend
   read -rs 'DATABASE_URL?Paste the External Database URL: ' && export DATABASE_URL
   .venv/bin/python -m scripts.create_admin
   unset DATABASE_URL
   ```

   (In bash the first line is `read -rsp 'Paste the External Database URL: ' DATABASE_URL && export DATABASE_URL`.)
   It prints `Target: database '…' on ….render.com`, then asks you to confirm, and for the
   email, full name and password.
4. Success ends with `Administrator <email> created in database '…'`. To double-check,
   run the command again: it answers `An administrator already exists` and changes nothing.
5. Sign in at https://patient-locker.vercel.app with that email and password.

**More staff accounts:** signed in as an administrator, open **Staff accounts**, fill in
**Add a staff member** and choose the role. The portal shows a one-time temporary password;
pass it to the person privately (not by email or chat in plain text). They must replace it
at first sign-in. **Reset password** on the same page issues a new temporary password.

**If an administrator already exists:** sign in as that administrator. A forgotten
password is reset by another administrator from the Staff page. A locked account unlocks
by itself after 15 minutes. If the only administrator can no longer sign in, deactivate
that account directly in the database (accounts are never deleted, so the audit history
is kept), then run the command again:

```sql
UPDATE users SET is_active = false WHERE email = 'old-admin@clinic.com';
```

## Roles

| Role | Can do |
|---|---|
| `admin` | Everything: manage staff accounts, read the audit log, see all patients |
| `case_manager` | See all patients, assign patients to staff, delete documents, resolve reviews |
| `staff` | Work on assigned patients only (and patients they create), resolve their reviews |

Anyone without access to a patient gets "not found", so the system never reveals that a
patient exists. Every refused attempt is recorded in the audit log.

## Document locker

- Accepted files: **PDF, JPEG and PNG**, up to 20 MB and 100 pages (configurable). The type
  is read from the file's content, never its name. Empty, damaged, password-protected,
  oversized or disguised files are refused with a clear reason.
- Files are encrypted (AES-256-GCM) before they reach disk and stored under random IDs.
- The same file cannot be uploaded twice for one patient.
- Replacing a document keeps the old version; deleting is a soft delete. Both appear in the
  document's history together with every view and download.

## Running

In three terminals:

```bash
make dev-api   # API on http://localhost:8000 (docs at /api/docs outside production)
make worker    # Background processing of uploads
make dev-web   # Portal on http://localhost:5173 (proxies /api to the API)
```

Without the worker, uploads are stored and queued but not processed; they are processed as
soon as a worker starts.

## Document processing

Every upload is queued for processing in the same database transaction as the upload itself.

1. **Text**: the PDF text layer is used where it exists; pages that are scans (little text, or
   mostly image) are OCR'd with Tesseract at 300 DPI. Photos are OCR'd directly.
2. **Fields**: labelled values (patient name and ID, dates of birth, admission and discharge,
   invoice number and date, doctor, total, policy and claim numbers) are read by rules, plus a
   count of billed line items.
3. **Category**: rules classify from the title line and characteristic phrases.
4. **AI profile** (when an LLM is configured): the model is asked the document type and printed
   names, then whether each page has a handwritten signature or a stamp. Names are checked
   against the text; pages the model could not assess are recorded as unchecked.

Documents with no usable text are marked `unreadable` and are never used as evidence. Failures
are retried with backoff and then recorded as `failed` with a reason. See the results at
`GET /api/v1/documents/{id}/analysis`.

## Query letters

Uploading an insurer's query letter (`POST /api/v1/patients/{id}/query-letters`) queues its
extraction in the same transaction; no further action is needed.

1. **Text** is extracted as for any document (OCR for scans).
2. **Candidates**: the letter body is split into list items and sentences. Address blocks,
   greetings, sign-offs and general instructions ("submit within 15 days") are skipped; every
   decision is stored with its reason.
3. **Points (rules)**: each candidate is read for named documents, split into separate points
   ("discharge summary and final bill" becomes two), with conditions attached to the right
   document ("signed by the treating doctor", "original", "itemised", "self-attested", ...)
   and clarification requests recognised.
4. **Points (model)**: the model is asked about each candidate separately as a second opinion.
   Points it confirms are marked `rules_and_model`; items only it found are kept and flagged
   for review; wording not present in the letter is ignored.
5. **Identity**: the claim number, policy number and patient name in the letter are compared
   with the profile; a mismatch flags the letter for review.

Every candidate produces at least one point, so no recognised request can be dropped silently.
Letter-wide notes such as "All documents should be attested" are kept as general conditions.

## Document matching

When extraction finishes, matching runs automatically (it waits while any of the patient's
documents are still being processed). Each query point gets one status, an explanation and
its evidence:

| Status | When |
|---|---|
| `COVERED` | A locker document of the right type, about the right subject, passes every condition. Evidence: document, page and the quoted line (plus the page of any signature or stamp). |
| `NEEDS_FROM_PATIENT` | Nothing in the locker qualifies and the system is sure: no such document, or a definite failure (e.g. the bill is a single lump sum when an itemised bill is asked for). Clarification requests are always this. |
| `NEEDS_REVIEW` | Anything uncertain: a condition that cannot be checked (signature marked "not authentic", "original", only one report for "serial" values), an unreadable or unclassified file that might be the one, a document naming another patient, or a point flagged at extraction. |

Checks: signature and stamp (from the page images, rejected if the page itself says the mark is
illustrative or not genuine); itemised (3+ billed lines); serial values (results on 2+ dates in
the stay); model/batch/serial number printed; policy period covering the admission date; and
subject words such as "ultrasound abdomen" or "left eye" (with synonyms like USG, OS).

Letter outcome: any `NEEDS_REVIEW` (or a letter-level issue such as an identity mismatch) holds
the whole letter for staff; otherwise any `NEEDS_FROM_PATIENT` means the missing-items message
is due; otherwise no additional documents are required. Staff resolve held points
(`POST /api/v1/query-points/{id}/review`) or confirm letter-level issues
(`POST /api/v1/query-letters/{id}/confirm`), and the outcome is recalculated. Staff decisions
are kept when a letter is matched again.

## Patient messages

When a letter's outcome is settled, the patient message is prepared automatically from the
templates in `backend/app/messaging/templates.py` (the "Patient Message Templates" document,
word for word): *documents needed* (WhatsApp and email) or *nothing needed*. A letter held for
review gets no message, and a prepared message is withdrawn if the letter goes back to review.

| Placeholder | Taken from |
|---|---|
| `patient_name` | Patient profile |
| `insurer_name` | Letterhead of the query letter (staff can correct: `PATCH /api/v1/query-letters/{id}`) |
| `claim_number` | Claim reference in the letter, else the patient profile |
| `missing_items` | NEEDS_FROM_PATIENT points, `1. Item: reason` |
| `due_date` | The letter's deadline ("within 15 days" of the letter date); if none, or already passed, today + `PATIENT_RESPONSE_DAYS` |
| `company_name`, `support_email`, `support_phone` | `.env` |

If any value is missing, the message is **blocked** and the letter goes to staff with the
missing fields listed. Item wording may only use an allowlist of administrative words, so
diagnoses, tests and treatments never appear; an item that would need them is described as,
for example, "Test report requested in the insurer's letter dated 24/09/2026". Every version
of the message is kept (`GET /api/v1/query-letters/{id}/messages`). Sending is Phase 9.

## Sending (WhatsApp and email)

A ready message is sent automatically on both channels in the same step that prepares it; there
is no send button. Each (letter, channel, message content) gets a unique key, so retries and
re-runs never send the same content twice. Temporary provider errors are retried with backoff;
permanent ones mark the letter for staff. Every status change (queued, sending, sent, delivered,
failed) is recorded with its time and shown on the patient profile.

`WHATSAPP_PROVIDER=fake` and `EMAIL_PROVIDER=fake` use the **local test outbox**: the message is
recorded and reported delivered, and nothing leaves the machine. The portal says so on every
delivery.

Real providers are built but **not yet tested live** (Phase 12, with the company's accounts):

| Setting | Provider | Needs | Delivery updates (point the provider here) |
|---|---|---|---|
| `WHATSAPP_PROVIDER=twilio` | Twilio WhatsApp | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` | `{PUBLIC_BASE_URL}/api/v1/webhooks/twilio/status` (set automatically per message) |
| `WHATSAPP_PROVIDER=meta` | Meta WhatsApp Cloud API | `META_WA_TOKEN`, `META_WA_PHONE_NUMBER_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN` | `{PUBLIC_BASE_URL}/api/v1/webhooks/meta/whatsapp` |
| `EMAIL_PROVIDER=resend` | Resend | `RESEND_API_KEY`, `EMAIL_FROM` (verified domain), `RESEND_WEBHOOK_SECRET` | `{PUBLIC_BASE_URL}/api/v1/webhooks/resend` |

Every webhook request must carry the provider's valid signature (Resend's also must be under five
minutes old); anything else is refused and recorded in the audit log. Status updates never move
backwards. WhatsApp conversations started by the business need an approved template on both
Twilio (outside the sandbox) and Meta; that is configured in Phase 12.

## The portal

http://localhost:5173 (with `make dev-api`, `make worker` and `make dev-web` running):

- **Dashboard**: patients, documents, letters, processing, awaiting review, completed, patients
  with missing documents, recent letters.
- **Patients**: search; create a patient (a Patient ID such as `PL-000004` is assigned).
- **Patient profile**: details; document locker (upload with or without a category; text is
  extracted and the document classified automatically); query letters (upload starts
  processing); every message sent to the patient with its delivery status and times.
- **Query details**: each point marked COVERED, NEEDS_FROM_PATIENT or NEEDS_REVIEW, with the
  letter's own sentence, the reason and the evidence (document, page, quoted line); staff
  decisions for NEEDS_REVIEW points; the message and its delivery; how the letter was read.
- **Review queue**: letters held until staff decide. Once resolved, sending is automatic.
- **Patient profile actions**: edit details, assign staff (admin, case manager), correct a
  document's category, reprocess or delete it (admin, case manager), correct a letter's insurer,
  claim number or deadline, match a letter again.
- **Staff accounts** (admin): create accounts with a one-time temporary password, change roles,
  deactivate, reset passwords.
- **Audit log** (admin): every recorded action, filterable; viewing it is itself recorded.

Screens refresh themselves while documents or letters are being processed.

## Security and privacy

- Sign-in: Argon2 passwords, server-side sessions (httpOnly, SameSite=Strict cookie, 30 min idle,
  12 h maximum), CSRF token on every change, 5 failures lock an account for 15 minutes, 20
  failures from one network address pause it for 15 minutes.
- Access: staff see only assigned patients; everyone else gets "not found". A test tries every
  route that takes an ID as an unassigned staff member and requires refusal, no data in the
  response and no change.
- Files: encrypted at rest (AES-256-GCM), type checked from content, served with no-store.
- Logs: names, contact details and document text are never logged; emails, phone numbers and URL
  query strings are redacted as a safety net. A test runs the full pipeline and searches the real
  log output for patient details.
- Audit log: append-only (enforced by a database trigger), personal data refused in its details.
- Dependencies: `pip-audit` and `npm audit` report no known vulnerabilities (2 Nov 2026).

## Measuring accuracy on new letters

```bash
make eval-letters LETTERS=~/path/to/letters KEY=~/path/to/answer_key.json
```

Prints each letter's extracted points and, with a key, the score. Nothing is stored or sent.

## Testing

```bash
make test      # pytest against the separate patient_locker_test database (fast, no model calls)
make test-llm  # slow checks against the real local model (needs Ollama)
E2E_EMAIL=you@x E2E_PASSWORD=... make test-e2e
               # the whole flow in real Chrome (services running); screenshots in e2e-screenshots/
make demo-files  # fictional demo patients and letters in demo/
make lint      # ruff
make build-web
```

The fictional sample documents in `backend/tests/fixtures/samples/` and the fictional query
letters in `backend/tests/fixtures/query_letters/` (with expected answers in
`expected.json`) are used by the tests. Letter 06 is held out: the rules are never adjusted to
it, so it measures how they do on unseen wording.

## Configuration

All settings come from environment variables (see `.env.example`). The API refuses to
start if a required secret is missing or invalid. `.env` and `storage/` (patient files)
are git-ignored and must never be committed.

## Deployment (Render + Vercel)

**API — Render web service** (settings recorded in `render.yaml`):

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt` |
| Start command | `sh scripts/render_start.sh` (migrates, starts the worker, then the API) |
| Health check path | `/api/v1/health` |
| Python | 3.12 (from `backend/.python-version`) |

Environment: `APP_ENV=production`, `DATABASE_URL` (the Render Postgres internal URL, used
as is), `FILE_ENCRYPTION_KEY` (32 random bytes, URL-safe base64; never change it once files
exist), `CORS_ORIGINS=https://patient-locker.vercel.app`, `LLM_PROVIDER=none`, and the
`COMPANY_NAME` / `SUPPORT_EMAIL` / `SUPPORT_PHONE` used in patient messages.

`/api/v1/health` returns `{"status":"ok","database":"ok"}` only when the database is
reachable *and* at the latest migration; otherwise 503 with `unavailable` or
`migrations_pending`.

Limits of the hosted setup:
- Uploaded files are kept on the service's disk. Attach a Render persistent disk (paid plan)
  and set `STORAGE_DIR` to it, or files are lost on each deploy or restart.
- Tesseract is not installed in Render's Python runtime, so scanned pages cannot be OCR'd
  there; PDFs with a text layer are unaffected.

First admin: see [First admin account](#first-admin-account). The command runs on your
machine against the database's External Database URL; it is never run during deployment.

**Portal — Vercel** (root directory `frontend`, settings in `frontend/vercel.json`): builds
with `npm run build` into `dist`. `/api/*` is forwarded to the Render API, so the portal and
API share one origin and the session cookie stays first-party; every other path serves the
app so links and page refreshes work.

## API so far

| Method | Path | Who |
|---|---|---|
| GET | `/` | Public (points to the health check) |
| GET | `/api/v1/health` | Public |
| POST | `/api/v1/auth/login` | Public |
| POST | `/api/v1/auth/logout` | Signed in |
| GET | `/api/v1/auth/me` | Signed in |
| POST | `/api/v1/auth/change-password` | Signed in |
| GET, POST | `/api/v1/users` | Admin |
| GET, PATCH | `/api/v1/users/{id}` | Admin |
| POST | `/api/v1/users/{id}/reset-password` | Admin |
| GET | `/api/v1/audit-logs` | Admin |
| GET | `/api/v1/staff-directory` | Admin, case manager |
| GET, POST | `/api/v1/patients` | Signed in (list shows accessible patients only) |
| GET, PATCH | `/api/v1/patients/{id}` | Patient access |
| GET | `/api/v1/patients/{id}/assignments` | Patient access |
| PUT | `/api/v1/patients/{id}/assignments` | Admin, case manager |
| GET, POST | `/api/v1/patients/{id}/documents` | Patient access (POST is a multipart upload) |
| GET, PATCH | `/api/v1/documents/{id}` | Patient access |
| GET | `/api/v1/documents/{id}/content[?download=true]` | Patient access |
| GET | `/api/v1/documents/{id}/history` | Patient access |
| GET | `/api/v1/documents/{id}/analysis` | Patient access (extracted text, fields, AI profile) |
| POST | `/api/v1/documents/{id}/reprocess` | Patient access |
| GET, POST | `/api/v1/patients/{id}/query-letters` | Patient access (POST is a multipart upload) |
| GET | `/api/v1/query-letters/{id}` | Patient access (points, identity check, extraction details) |
| POST | `/api/v1/query-letters/{id}/reprocess` | Patient access |
| POST | `/api/v1/query-letters/{id}/rematch` | Patient access |
| PATCH | `/api/v1/query-letters/{id}` | Patient access (insurer name, claim reference, deadline days) |
| GET | `/api/v1/query-letters/{id}/messages` | Patient access |
| POST | `/api/v1/query-letters/{id}/confirm` | Patient access + review permission |
| POST | `/api/v1/query-points/{id}/review` | Patient access + review permission |
| DELETE | `/api/v1/documents/{id}` | Admin, case manager |

Interactive documentation: http://localhost:8000/api/docs (disabled in production).

## Project layout

```
backend/
  app/
    api/         HTTP routes (v1/) and request guards (deps.py)
    core/        settings, logging, security, roles and permissions
    db/          SQLAlchemy base and sessions
    ai/          LLM client (switchable provider) and the document profile
    models/      database tables
    pipeline/    job queue and background worker
    processing/  file validation, text extraction, OCR, classification, field reading
    schemas/     request and response shapes
    services/    business logic (auth, users, audit, patients, documents, processing, storage)
  alembic/       migrations
  scripts/       local environment helpers
  tests/
frontend/        React portal
docs/            architecture and decisions
```
