// Shapes returned by the API (see /api/docs for the full schema).

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Patient {
  id: string;
  patient_code: string;
  full_name: string;
  phone: string;
  email: string;
  date_of_birth: string | null;
  policy_number: string | null;
  claim_number: string | null;
  created_at: string;
  updated_at: string;
}

export interface PatientListItem extends Patient {
  document_count: number;
  latest_letter_status: string | null;
}

export interface LockerDocument {
  id: string;
  patient_id: string;
  kind: "supporting" | "query_letter";
  declared_category: string | null;
  detected_category: string | null;
  classification_confidence: number | null;
  classification_method: string | null;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  page_count: number;
  processing_status: "uploaded" | "processing" | "processed" | "unreadable" | "failed";
  processing_error: string | null;
  processed_at: string | null;
  uploaded_by_name: string;
  uploaded_at: string;
}

export interface Evidence {
  document_id: string;
  filename: string;
  page?: number;
  quote?: string;
  verified?: boolean;
  kind?: string;
  source?: string;
}

export interface Match {
  id: string;
  status: "covered" | "needs_from_patient" | "needs_review";
  explanation: string;
  evidence: Evidence[];
  condition_checks: { code: string; result: string; detail: string }[];
  decided_by: "system" | "staff";
  created_at: string;
}

export interface QueryPoint {
  id: string;
  seq: number;
  requirement: string;
  requirement_type: string;
  conditions: { code: string; text: string }[];
  source_text: string;
  source: string;
  needs_review: boolean;
  review_reason: string | null;
  dismissed_at: string | null;
  current_match: Match | null;
}

export interface NotificationEvent {
  status: string;
  detail: string | null;
  occurred_at: string;
}

export interface Notification {
  id: string;
  channel: "whatsapp" | "email";
  provider: string;
  recipient: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
  events: NotificationEvent[];
}

export interface PatientMessage {
  id: string;
  query_letter_id: string;
  kind: "documents_needed" | "nothing_needed";
  status: "ready" | "blocked";
  blocked_reasons: string[];
  whatsapp_text: string | null;
  email_subject: string | null;
  email_text: string | null;
  values: Record<string, string | null>;
  created_at: string;
  superseded_at: string | null;
  notifications: Notification[];
}

export interface QueryLetter {
  id: string;
  patient_id: string;
  document_id: string;
  original_filename: string;
  status: string;
  outcome: string | null;
  identity_status: string | null;
  needs_review: boolean;
  review_reasons: string[];
  letter_date: string | null;
  claim_reference: string | null;
  insurer_name: string | null;
  response_days: number | null;
  point_count: number;
  error: string | null;
  created_at: string;
  extracted_at: string | null;
  matched_at: string | null;
}

export interface QueryLetterDetail extends QueryLetter {
  identity_details: Record<string, { result: string }>;
  extraction_details: {
    segments?: { index: number; text: string; used: boolean; reason: string }[];
    general_conditions?: { text: string }[];
    warnings?: string[];
    method?: string;
  };
  points: QueryPoint[];
  current_message: PatientMessage | null;
}

export interface DashboardStats {
  total_patients: number;
  total_documents: number;
  total_query_letters: number;
  letters_processing: number;
  letters_awaiting_review: number;
  letters_completed: number;
  letters_failed: number;
  patients_with_missing_documents: number;
}

export interface LetterRow {
  id: string;
  patient_id: string;
  patient_code: string;
  patient_name: string;
  status: string;
  outcome: string | null;
  review_reasons: string[];
  insurer_name: string | null;
  claim_reference: string | null;
  created_at: string;
}

export interface PatientMessageRow {
  letter_id: string;
  letter_date: string | null;
  insurer_name: string | null;
  message: PatientMessage;
}
