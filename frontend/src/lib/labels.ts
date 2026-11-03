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
  covered: ["COVERED", "green"],
  needs_from_patient: ["NEEDS_FROM_PATIENT", "violet"],
  needs_review: ["NEEDS_REVIEW", "amber"],
};

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
