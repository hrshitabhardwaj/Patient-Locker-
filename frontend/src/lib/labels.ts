// Human wording and colours for codes returned by the API.

export type Tone = "grey" | "blue" | "green" | "amber" | "red" | "violet";

export const CATEGORY_LABEL: Record<string, string> = {
  discharge_summary: "Discharge summary",
  hospital_bill: "Hospital bill",
  investigation_report: "Investigation report",
  consultation_paper: "Consultation paper",
  pharmacy_bill: "Pharmacy bill",
  policy_copy: "Policy copy",
  health_card: "Health card",
  kyc_document: "KYC document",
  other: "Other",
  clarification: "Clarification",
};

export const categoryLabel = (code: string | null | undefined) =>
  code ? (CATEGORY_LABEL[code] ?? code) : "—";

export const DOCUMENT_STATUS: Record<string, [string, Tone]> = {
  uploaded: ["Queued", "blue"],
  processing: ["Processing…", "blue"],
  processed: ["Processed", "green"],
  unreadable: ["Unreadable", "amber"],
  failed: ["Failed", "red"],
};

export const LETTER_STATUS: Record<string, [string, Tone]> = {
  received: ["Received", "blue"],
  extracting: ["Reading letter…", "blue"],
  extracted: ["Matching…", "blue"],
  matching: ["Matching…", "blue"],
  awaiting_review: ["Needs staff review", "amber"],
  ready_to_notify: ["Sending…", "blue"],
  no_action_required: ["Sending…", "blue"],
  notified: ["Patient notified", "green"],
  failed: ["Failed", "red"],
};

export const OUTCOME: Record<string, [string, Tone]> = {
  send_missing_items_message: ["Missing items requested", "violet"],
  hold_for_staff_review: ["Held for staff review", "amber"],
  no_additional_documents_required: ["No documents needed", "green"],
};

export const MATCH_STATUS: Record<string, [string, Tone]> = {
  covered: ["Already in locker", "green"],
  needs_from_patient: ["Ask the patient", "violet"],
  needs_review: ["Staff to check", "amber"],
};

/** Document types every claim usually needs, shown as the locker checklist. */
export const LOCKER_CHECKLIST: { label: string; codes: string[] }[] = [
  { label: "Discharge summary", codes: ["discharge_summary"] },
  { label: "Hospital bill", codes: ["hospital_bill"] },
  { label: "Pharmacy bills", codes: ["pharmacy_bill"] },
  { label: "Investigation reports", codes: ["investigation_report"] },
  { label: "Consultation papers", codes: ["consultation_paper"] },
  { label: "Policy copy / health card", codes: ["policy_copy", "health_card"] },
  { label: "KYC", codes: ["kyc_document"] },
];

/** "discharge_summary__ds.pdf" (category prefix from bulk import) → "ds.pdf". */
export function displayName(filename: string): string {
  const cut = filename.indexOf("__");
  return cut > 0 && CATEGORY_LABEL[filename.slice(0, cut)] ? filename.slice(cut + 2) : filename;
}

/** Removes the same prefix from file names quoted inside a sentence. */
export function cleanFileNames(text: string): string {
  return text.replace(/\b([a-z_]+)__(?=[\w.-]+\.(?:pdf|png|jpe?g)\b)/gi, (whole, prefix: string) =>
    CATEGORY_LABEL[prefix] ? "" : whole,
  );
}

/** "+919812345678" → "+91 98••••5678": enough to recognise, not enough to copy. */
export function maskPhone(phone: string): string {
  if (phone.length < 8) return phone;
  return `${phone.slice(0, 3)} ${phone.slice(3, 5)}••••${phone.slice(-4)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  }
  const hours = Math.floor(seconds / 3600);
  return `${hours} h ${Math.round((seconds % 3600) / 60)} min`;
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function secondsBetween(from: string, to: string): number {
  return Math.max(0, (new Date(to).getTime() - new Date(from).getTime()) / 1000);
}

export const NOTIFICATION_STATUS: Record<string, [string, Tone]> = {
  queued: ["Queued", "blue"],
  sending: ["Sending…", "blue"],
  sent: ["Sent", "blue"],
  delivered: ["Delivered", "green"],
  read: ["Read", "green"],
  failed: ["Failed", "red"],
};

export const REVIEW_REASON: Record<string, string> = {
  identity_mismatch: "The claim number or name in the letter does not match this patient.",
  no_requirements_found: "No requirements could be found in the letter.",
  letter_unreadable: "The letter could not be read.",
  points_need_review: "Some points need a staff decision.",
  message_values_missing: "A value needed for the patient message is missing.",
  notification_failed: "Sending to the patient failed.",
  matching_failed: "Matching could not be completed.",
  message_preparation_failed: "The patient message could not be prepared.",
};

/** Letter statuses where work is still happening, so screens keep refreshing. */
export const LETTER_IN_PROGRESS = new Set([
  "received",
  "extracting",
  "extracted",
  "matching",
  "ready_to_notify",
  "no_action_required",
]);

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
}
