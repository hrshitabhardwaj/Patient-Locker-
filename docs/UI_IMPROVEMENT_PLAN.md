# UI improvement plan

Goal: make the portal easy to understand at a glance and make the automation visible on
screen. Each item names the judging criterion it serves and the files it touches. All items
are frontend-only: every value they need is already returned by the API.

Judging criteria served: ease of use (15), speed (5), care with patient data (10), message
clarity (10). The automation itself (15) and accuracy (45) are already built; the UI's job is
to make them easy to see.

## What works today

- Clean layout, consistent cards and badges, sticky sidebar.
- Query details page: each point shows the letter's own sentence, a status, the reason and
  the evidence (document, page, quoted line).
- WhatsApp and email previews side by side, with per-channel delivery status and history.
- Screens refresh themselves while documents and letters are being processed.

## Problems found

1. Statuses appear as raw codes (`NEEDS_FROM_PATIENT`, `COVERED`), including in the
   explanation banner. Staff and judges have to translate them.
2. The automation is invisible. Nothing on screen shows "uploaded at 11:31:02, read, checked,
   WhatsApp sent at 11:31:08". That is the core of the product and of the speed score.
3. On the patient profile the upload forms take the most space. The current case status
   (what's pending, was the patient messaged) has to be pieced together from three cards
   further down.
4. The dashboard gives eight tiles equal weight. Zero counts are shown in red or orange, and
   nothing says "this needs you now".
5. The patient list doesn't show each case's status, so you have to open every patient.
6. Document names show the upload prefix (`discharge_summary__discharge_summary.pdf`) and the
   evidence quote is often only the title line.
7. Patient data care is strong but invisible: access rules, encryption, audit and the "no
   medical details in messages" rule are not visible anywhere.

## Priority 1: before recording the video

| # | Change | Criterion | Files | Size |
|---|---|---|---|---|
| 1 | **Plain-language statuses.** `covered` → "Already in locker" (green), `needs_from_patient` → "Ask the patient" (violet), `needs_review` → "Staff to check" (amber). Rewrite the banner: "The patient is asked only for items marked *Ask the patient*. Items already in the locker are never requested again." | Ease of use, clarity | `lib/labels.ts`, `pages/QueryLetterPage.tsx` | S |
| 2 | **Automation timeline** at the top of the query letter page: Uploaded → Read (N points found) → Checked against N locker documents → Message prepared → WhatsApp sent / delivered → Email sent, each with its time, plus "Upload to message: 6 seconds" and "No staff action needed". For held letters: "Waiting for staff: 1 point to check". Uses `created_at`, `extracted_at`, `matched_at`, message `created_at`, notification `sent_at` / `delivered_at`. | Speed, automation | new `components/Timeline.tsx`, `pages/QueryLetterPage.tsx` | M |
| 3 | **Case summary strip** at the top of the patient profile: latest letter's outcome, number of items requested, WhatsApp and email status with times, and a "Waiting for staff" link if held. Below it, order the cards: Query letters → Messages → Document locker → Details. Collapse the two upload forms behind "Upload document" / "Upload query letter" buttons. | Ease of use | `pages/PatientPage.tsx` | M |
| 4 | **Locker checklist** inside the document locker: one chip per document type (Discharge summary ✓, Hospital bill ✓, Investigation report ✓, Policy copy —, KYC —, …) with counts. Shows at a glance what the locker holds, which is the decision the system makes. | Ease of use | `pages/PatientPage.tsx`, `lib/labels.ts` | S |
| 5 | **Dashboard focus.** A "Needs attention" row first (awaiting staff review, failed deliveries, failed letters), coloured only when above zero. A "Median time from upload to message" tile. A "Recent messages sent" feed (patient ID, channel, status, time). Keep the totals as a smaller row. | Ease of use, speed | `pages/DashboardPage.tsx`, `services/dashboard.py` (one extra field) | M |
| 6 | **Status column on the patient list**: latest letter outcome chip and number of pending items. Show phones masked in the list (`+91 98•••• 5678`); the full number stays on the profile. | Ease of use, data care | `pages/PatientsPage.tsx` | S |

## Priority 2: if time allows

| # | Change | Criterion | Files | Size |
|---|---|---|---|---|
| 7 | **Clean document names**: show the category label and the original name without the `category__` prefix. In evidence, prefer the matched line over the title line. | Clarity | `pages/PatientPage.tsx`, `pages/QueryLetterPage.tsx` | S |
| 8 | **Review queue with reasons**: each held letter lists its "Staff to check" points and their reasons, with "Ask the patient" / "Already in locker" buttons inline, so staff resolve it without opening the letter. | Ease of use | `pages/ReviewQueuePage.tsx` | M |
| 9 | **Visible privacy cues**: on the patient header, "You can see this patient as: Administrator / assigned staff"; a footer line "Files are encrypted. Every view and download is recorded in the audit log."; a note under the message preview: "Messages never mention diagnoses or tests, because phone previews can be seen by others." | Data care | `pages/PatientPage.tsx`, `components/MessageView.tsx` | S |
| 10 | **Delivery cards**: show "via Twilio WhatsApp" and "via Brevo" in place of the test-outbox notice once live, plus a WhatsApp-style bubble with read ticks for delivered. | Clarity | `components/MessageView.tsx` | S |
| 11 | **"Letter processed" toast** while a letter is being processed: "Letter for PL-000003 checked: 2 items requested, WhatsApp and email sent". | Speed, automation | `components/Layout.tsx` | S |

## Priority 3: polish

- Branded sign-in page (product name, one-line description, "Authorised staff only").
- Check the layout at phone width; tables become stacked rows.
- Empty states that say what to do next ("No documents yet. Upload the discharge summary,
  bills and reports first").
- Accessibility pass: badge contrast, focus rings, labels on file inputs.

## How to check each change

- `npm run build` (type check and build) after each item.
- `make test-e2e` re-runs the browser walkthrough and refreshes `e2e-screenshots/` for a visual
  before and after.
- No backend behaviour changes, except item 5's one extra dashboard field (add a test for it).
