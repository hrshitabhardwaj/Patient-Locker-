# Patient Locker: walkthrough video script

Length: about 5½ minutes. Each scene gives what is on screen and what you say. Lines tagged
**[UI #n]** point to that item of `docs/UI_IMPROVEMENT_PLAN.md` (items 1–6 are built). Values in `[brackets]` are filled in from
your own dry run: never say a number you haven't seen on screen.

---

## Before you record

- [ ] Live Twilio, Brevo and Gemini keys set on Render. One test message has reached your
      phone and inbox.
- [ ] Your phone has sent `join <code>` to the Twilio sandbox **today** (WhatsApp only
      delivers free-form messages within 24 hours of your last message to the sandbox).
- [ ] Patients loaded **the day before** with `make import-patients`, **without** letters,
      with your phone and email as the contact. The documents are processed and show
      "Processed".
- [ ] The three practice letters saved on the desktop, ready to upload live:
      `QUERY_LETTER_letter_01.pdf` (Rohan Mehtani), `…_02.pdf` (Neha Sarangi),
      `…_03.pdf` (Arjun Bhatnagar).
- [ ] A full dry run done. Rohan's letter needs the Gemini signature check to send
      automatically. If your dry run held it for review, use it in Scene 5 instead and pick
      another letter for Scene 3.
- [ ] Phone mirrored on screen (iPhone: QuickTime → New Movie Recording → choose the phone;
      Android: scrcpy), with WhatsApp open. Email inbox open in a second browser tab.
- [ ] Browser zoom at 110–125%, notifications off, signed in as an administrator.
- [ ] Open the portal once a few minutes before recording, so the server is awake.

---

## Scene 1: The problem (0:00–0:25)

**Screen:** title slide, or the portal's sign-in page.

> When an insurer reviews a claim, they often send back a query letter asking for more
> documents. Today someone has to read that letter, check what we already have, work out
> what's missing, and message the patient. It's slow, and when it goes wrong, patients get
> asked for the wrong thing, or for something they already sent us.
>
> Patient Locker does that whole job on its own, in seconds.

## Scene 2: The locker (0:25–1:20)

**Screen:** sign in → Dashboard → Patients → open **Rohan Mehtani**.

> Every patient has a profile linked to a patient ID (this is PL-000001) and a secure
> locker holding all their claim documents: discharge summary, bills, reports, policy copy,
> KYC.

**Screen:** scroll to the document locker; point at the checklist chips **[UI #4]**.

> When a document is uploaded, the system reads it, using OCR if it's a scan, and works
> out what kind of document it is. Here it has recognised a discharge summary, a hospital
> bill and an ultrasound report. At a glance we can see what the locker holds and what it
> doesn't.

**Screen:** point at the patient list status column **[UI #6]**.

> And from the patient list, every case shows its status, so nobody has to open each file
> to know where things stand.

## Scene 3: A query letter arrives (1:20–3:00), the main demo

**Screen:** on Rohan's profile, click **Upload query letter**, choose
`QUERY_LETTER_letter_01.pdf`, upload. Keep the phone mirror visible.

> Now the insurer, Kestrelline Health, has sent a query letter. All I do is add it to
> Rohan's locker. From here, nobody on the team clicks anything.

**Screen:** open the letter; the timeline fills in **[UI #2]**.

> Watch the timeline. The letter is read, each separate request is pulled out, every one is
> checked against the documents already in the locker, and the message is written and sent.

**Screen:** WhatsApp message arrives on the phone; switch to the inbox and open the email.

> And there it is on WhatsApp, and the same message by email: `[N]` seconds from upload,
> with no manual step.

**Screen:** back to the query details; scroll through the four points slowly.

> Let's see what it decided, and why. The insurer raised four points.
>
> **One:** a discharge summary signed and stamped by the treating doctor. We already have
> it. The system found the signature and the hospital stamp on page 1, so it doesn't ask
> again.
>
> **Two:** an itemised final hospital bill. We do have a bill, but it's a single lump sum,
> not itemised. So this one is needed from the patient, and the message says exactly why.
>
> **Three:** the ultrasound report of the abdomen. Already in the locker, so not requested.
>
> **Four:** the date the symptoms first started. Only the patient can answer that, so we ask.
>
> Every decision shows its evidence: which document, which page, and the line it relied on.
> Nothing is a black box.

**Screen:** scroll to the WhatsApp and email preview.

> The message follows the fixed template word for word, in plain language. It lists only
> the two missing items, says why each one is needed and by when, and tells Rohan he doesn't
> need to resend anything else. It also never mentions a diagnosis or a test name, because
> message previews on a phone can be seen by other people.

## Scene 4: When nothing is needed (3:00–3:30)

**Screen:** open **Arjun Bhatnagar**, upload `QUERY_LETTER_letter_03.pdf`, wait for the
timeline, then show the phone.

> Here's a different case. Arjun's insurer asks for four things, and all four are already
> in his locker, including a lens invoice that shows the model number and a policy copy
> covering his admission date. So instead of a request, Arjun gets a short note: we have
> everything, you don't need to send anything. That saves a patient a worried phone call.

## Scene 5: When the system isn't sure (3:30–4:20)

**Screen:** open **Neha Sarangi**, upload `QUERY_LETTER_letter_02.pdf`. The status shows
"Waiting for staff"; open the point marked "Staff to check" **[UI #1]**.

> Accuracy matters more than speed, so when the system isn't certain, it doesn't guess.
> Neha's insurer wants serial platelet counts for her whole hospital stay. The locker has a
> blood count report, but only from one day, so the system can't be sure it covers the
> whole stay. It holds the letter instead of sending a wrong message.

**Screen:** Review queue → open the letter → choose "Ask the patient for it" on that point →
show the phone receiving the message.

> A staff member makes that one decision. The moment they do, the message goes out
> automatically on both channels, with that report added to the list. Notice that it says
> "test report", not the test's name: again, no medical details in a message preview.

## Scene 6: Who can see what (4:20–5:05)

**Screen:** Staff accounts page (roles), then the audit log filtered to Rohan.

> This is medical and financial data, so access is strict. Staff see only the patients
> assigned to them. To anyone else, a patient simply doesn't exist: they get "not found",
> not "access denied". Administrators and case managers see everyone.
>
> Every file is encrypted before it is stored. Every view, download and decision is
> written to an audit log that can't be edited, and the server logs never contain names,
> phone numbers or document text. Sign-in uses expiring sessions and locks out repeated
> wrong passwords.

## Scene 7: Close (5:05–5:30)

**Screen:** Dashboard with the "Needs attention" row and the "upload to message" tile
**[UI #5]**.

> So, to recap. Patient Locker reads every query letter, finds each separate request,
> checks it against what we already have, asks the patient only for what's genuinely
> missing, in plain language and on the fixed template, and sends it on WhatsApp and email
> in seconds, with no one clicking anything. When it isn't sure, it asks a person first.
>
> That's Patient Locker. Thank you.

---

## Recording tips

- Record each scene separately and join them afterwards; re-record only the scene that
  goes wrong.
- If a live send is slow, keep talking over the decisions (Scene 3) and cut to the phone
  when the message arrives, rather than leaving dead air.
- Say "the system decided", not "the AI decided": most decisions come from checks you can
  see on screen, which is a strength worth emphasising.
- Don't show the `.env` file, Render settings or real API keys on screen.
