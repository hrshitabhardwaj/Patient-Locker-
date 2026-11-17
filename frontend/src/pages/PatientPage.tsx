import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

import MessageView from "../components/MessageView";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBox,
  inputClass,
  Labelled,
  LetterBadges,
  Notice,
  Spinner,
  StatusBadge,
} from "../components/ui";
import { api, upload } from "../lib/api";
import {
  CATEGORY_LABEL,
  categoryLabel,
  displayName,
  DOCUMENT_STATUS,
  formatDateTime,
  formatSize,
  LETTER_IN_PROGRESS,
  LOCKER_CHECKLIST,
  NOTIFICATION_STATUS,
} from "../lib/labels";
import type { LockerDocument, Patient, PatientMessageRow, QueryLetter, QueryLetterDetail } from "../lib/types";

const DOCUMENT_CATEGORIES = Object.entries(CATEGORY_LABEL).filter(([code]) => code !== "clarification");

function UploadBox({
  label,
  hint,
  withCategory,
  onUpload,
}: {
  label: string;
  hint: string;
  withCategory?: boolean;
  onUpload: (file: File, category: string) => Promise<unknown>;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [category, setCategory] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const mutation = useMutation({
    mutationFn: () => onUpload(file!, category),
    onSuccess: () => {
      setFile(null);
      setCategory("");
      if (fileInput.current) fileInput.current.value = "";
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (file) mutation.mutate();
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1">
          <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
          <input
            ref={fileInput}
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-sky-700 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-white"
          />
        </label>
        {withCategory && (
          <label className="w-56">
            <span className="mb-1 block text-sm font-medium text-slate-700">Category</span>
            <select value={category} onChange={(e) => setCategory(e.target.value)} className={inputClass}>
              <option value="">Detect automatically</option>
              {DOCUMENT_CATEGORIES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
        <Button type="submit" disabled={!file || mutation.isPending}>
          {mutation.isPending ? "Uploading…" : "Upload"}
        </Button>
      </div>
      <p className="text-xs text-slate-500">{hint}</p>
      <ErrorBox error={mutation.error} />
    </form>
  );
}

function usePermission(permission: string): boolean {
  const { state } = useAuth();
  return state.status === "signed_in" && state.user.permissions.includes(permission);
}

function DocumentActions({ doc, patientId }: { doc: LockerDocument; patientId: string }) {
  const queryClient = useQueryClient();
  const canDelete = usePermission("documents:delete");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents", patientId] });
  const recategorise = useMutation({
    mutationFn: (category: string) =>
      api(`/documents/${doc.id}`, { method: "PATCH", body: { declared_category: category || null } }),
    onSuccess: refresh,
  });
  const reprocess = useMutation({
    mutationFn: () => api(`/documents/${doc.id}/reprocess`, { method: "POST" }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api(`/documents/${doc.id}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  return (
    <div className="flex flex-wrap items-center gap-1">
      <select
        aria-label="Change category"
        value={doc.declared_category ?? ""}
        onChange={(e) => recategorise.mutate(e.target.value)}
        className="rounded-md border border-slate-300 bg-white px-1.5 py-1 text-xs"
      >
        <option value="">Auto-detected</option>
        {DOCUMENT_CATEGORIES.map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
      <Button variant="ghost" className="text-xs" onClick={() => reprocess.mutate()} disabled={reprocess.isPending}>
        Reprocess
      </Button>
      {canDelete && (
        <Button
          variant="ghost"
          className="text-xs text-red-700 hover:bg-red-50"
          onClick={() => {
            if (window.confirm(`Remove "${displayName(doc.original_filename)}" from the locker? It is kept in the history.`)) {
              remove.mutate();
            }
          }}
        >
          Delete
        </Button>
      )}
      <ErrorBox error={recategorise.error ?? reprocess.error ?? remove.error} />
    </div>
  );
}

function Locker({ patientId }: { patientId: string }) {
  const queryClient = useQueryClient();
  const documents = useQuery({
    queryKey: ["documents", patientId],
    queryFn: () => api<LockerDocument[]>(`/patients/${patientId}/documents`),
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.processing_status === "uploaded" || d.processing_status === "processing")
        ? 2000
        : 15000,
  });
  const supporting = documents.data?.filter((d) => d.kind === "supporting") ?? [];
  const [uploading, setUploading] = useState(false);
  const showUpload = uploading || (documents.isSuccess && supporting.length === 0);

  return (
    <Card
      title={`Document locker (${supporting.length})`}
      actions={
        !showUpload && (
          <Button variant="secondary" onClick={() => setUploading(true)}>
            + Upload document
          </Button>
        )
      }
    >
      {showUpload && (
        <div className="mb-4">
          <UploadBox
            label="Upload a document"
            hint="PDF, JPEG or PNG, up to 20 MB. Text is extracted (OCR for scans) and the document is classified automatically."
            withCategory
            onUpload={async (file, category) => {
              await upload(`/patients/${patientId}/documents`, file, { category });
              queryClient.invalidateQueries({ queryKey: ["documents", patientId] });
            }}
          />
        </div>
      )}
      {supporting.length > 0 && <LockerChecklist documents={supporting} />}
      <div className="mt-4">
        {documents.isLoading ? (
          <Spinner label="Loading…" />
        ) : !supporting.length ? (
          <Empty>No documents yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500 uppercase">
                <tr>
                  <th className="py-2 pr-4">Document</th>
                  <th className="py-2 pr-4">Category</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Uploaded</th>
                  <th className="py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {supporting.map((doc) => {
                  const detected = doc.detected_category;
                  const mismatch = doc.declared_category && detected && doc.declared_category !== detected;
                  return (
                    <tr key={doc.id} className="align-top">
                      <td className="py-2 pr-4">
                        <a
                          href={`/api/v1/documents/${doc.id}/content`}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-sky-800 hover:underline"
                        >
                          {displayName(doc.original_filename)}
                        </a>
                        <span className="block text-xs text-slate-500">
                          {doc.page_count} page(s) · {formatSize(doc.size_bytes)} ·{" "}
                          <a href={`/api/v1/documents/${doc.id}/content?download=true`} className="hover:underline">
                            download
                          </a>
                        </span>
                      </td>
                      <td className="py-2 pr-4">
                        {categoryLabel(doc.declared_category ?? detected)}
                        <span className="block text-xs text-slate-500">
                          {doc.declared_category
                            ? detected
                              ? `chosen by staff · detected: ${categoryLabel(detected)}`
                              : "chosen by staff"
                            : detected
                              ? `detected (${Math.round((doc.classification_confidence ?? 0) * 100)}% confidence)`
                              : doc.processing_status === "processed"
                                ? "not recognised"
                                : ""}
                        </span>
                        {mismatch && <Badge tone="amber">Check category</Badge>}
                      </td>
                      <td className="py-2 pr-4">
                        <StatusBadge map={DOCUMENT_STATUS} code={doc.processing_status} />
                        {doc.processing_error && (
                          <span className="block text-xs text-slate-500">{doc.processing_error.replaceAll("_", " ")}</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-slate-500">
                        {formatDateTime(doc.uploaded_at)}
                        <span className="block text-xs">{doc.uploaded_by_name}</span>
                      </td>
                      <td className="py-2">
                        <DocumentActions doc={doc} patientId={patientId} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

function QueryLetters({ patientId }: { patientId: string }) {
  const queryClient = useQueryClient();
  const letters = useQuery({
    queryKey: ["letters", patientId],
    queryFn: () => api<QueryLetter[]>(`/patients/${patientId}/query-letters`),
    refetchInterval: (query) =>
      query.state.data?.some((l) => LETTER_IN_PROGRESS.has(l.status)) ? 2000 : 15000,
  });

  const [uploading, setUploading] = useState(false);
  const showUpload = uploading || (letters.isSuccess && letters.data.length === 0);

  return (
    <Card
      title="Insurer query letters"
      actions={
        !showUpload && (
          <Button onClick={() => setUploading(true)}>+ Upload query letter</Button>
        )
      }
    >
      {showUpload && (
        <div className="mb-4">
          <UploadBox
            label="Upload the insurer's query letter"
            hint="Everything after this is automatic: the letter is read, each request is checked against the locker, and the patient is messaged on WhatsApp and email. Only uncertain points wait for staff."
            onUpload={async (file) => {
              await upload(`/patients/${patientId}/query-letters`, file);
              queryClient.invalidateQueries({ queryKey: ["letters", patientId] });
              queryClient.invalidateQueries({ queryKey: ["patient-messages", patientId] });
              setUploading(false);
            }}
          />
        </div>
      )}
      <div>
        {letters.isLoading ? (
          <Spinner label="Loading…" />
        ) : !letters.data?.length ? (
          <Empty>No query letters yet.</Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {letters.data.map((letter) => (
              <li key={letter.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <Link to={`/letters/${letter.id}`} className="font-medium text-sky-800 hover:underline">
                    {letter.insurer_name ?? letter.original_filename}
                  </Link>
                  <p className="text-xs text-slate-500">
                    {letter.claim_reference ? `Claim ${letter.claim_reference} · ` : ""}
                    {letter.letter_date ? `Letter dated ${letter.letter_date} · ` : ""}
                    {letter.point_count} point(s) · uploaded {formatDateTime(letter.created_at)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {LETTER_IN_PROGRESS.has(letter.status) && <Spinner />}
                  <LetterBadges status={letter.status} outcome={letter.outcome} />
                  <Link to={`/letters/${letter.id}`}>
                    <Button variant="secondary">Open</Button>
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function LockerChecklist({ documents }: { documents: LockerDocument[] }) {
  const usable = documents.filter((d) => d.processing_status !== "failed");
  const countFor = (codes: string[]) =>
    usable.filter((d) => codes.includes(d.declared_category ?? d.detected_category ?? "")).length;
  const known = new Set(LOCKER_CHECKLIST.flatMap((item) => item.codes));
  const others = usable.filter((d) => !known.has(d.declared_category ?? d.detected_category ?? "")).length;
  return (
    <div className="flex flex-wrap gap-2" aria-label="What the locker holds">
      {LOCKER_CHECKLIST.map((item) => {
        const n = countFor(item.codes);
        return (
          <span
            key={item.label}
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${
              n ? "bg-emerald-50 text-emerald-800 ring-emerald-200" : "bg-slate-50 text-slate-400 ring-slate-200"
            }`}
          >
            <span aria-hidden>{n ? "✓" : "–"}</span>
            {item.label}
            {n > 1 && <span className="text-emerald-600">×{n}</span>}
            <span className="sr-only">{n ? `${n} in locker` : "not in locker"}</span>
          </span>
        );
      })}
      {others > 0 && (
        <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
          Other ×{others}
        </span>
      )}
    </div>
  );
}

function CaseSummary({ patientId }: { patientId: string }) {
  const letters = useQuery({
    queryKey: ["letters", patientId],
    queryFn: () => api<QueryLetter[]>(`/patients/${patientId}/query-letters`),
    refetchInterval: (query) =>
      query.state.data?.some((l) => LETTER_IN_PROGRESS.has(l.status)) ? 2000 : 15000,
  });
  const latest = [...(letters.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const detail = useQuery({
    queryKey: ["letter", latest?.id],
    queryFn: () => api<QueryLetterDetail>(`/query-letters/${latest!.id}`),
    enabled: Boolean(latest),
    refetchInterval: (query) =>
      query.state.data && LETTER_IN_PROGRESS.has(query.state.data.status) ? 2000 : 10000,
  });

  if (letters.isLoading) return null;
  if (!latest) {
    return (
      <Notice tone="grey">
        No query letter yet. When the insurer's letter arrives, upload it below: checking and messaging the
        patient happen automatically.
      </Notice>
    );
  }
  const d = detail.data;
  const live = d?.points.filter((p) => !p.dismissed_at) ?? [];
  const toAsk = live.filter((p) => p.current_match?.status === "needs_from_patient").length;
  const toCheck = live.filter((p) => p.current_match?.status === "needs_review").length;
  const inProgress = LETTER_IN_PROGRESS.has(latest.status);
  const notifications = d?.current_message?.notifications ?? [];

  let headline: string;
  if (inProgress) headline = "Checking the latest letter…";
  else if (latest.status === "awaiting_review")
    headline = toCheck ? `Waiting for staff: ${toCheck} point(s) to check` : "Waiting for a staff check";
  else if (latest.outcome === "no_additional_documents_required") headline = "Nothing needed from the patient";
  else if (latest.outcome === "send_missing_items_message")
    headline = `${toAsk} item(s) requested from the patient`;
  else if (latest.status === "failed") headline = "Processing failed: open the letter";
  else headline = "Latest letter";

  const tone =
    latest.status === "awaiting_review" || latest.status === "failed"
      ? "border-amber-300 bg-amber-50"
      : latest.outcome === "no_additional_documents_required"
        ? "border-emerald-200 bg-emerald-50"
        : "border-sky-200 bg-sky-50";

  return (
    <section className={`flex flex-wrap items-center justify-between gap-4 rounded-xl border p-4 ${tone}`}>
      <div className="min-w-0">
        <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Case status</p>
        <p className="mt-0.5 flex items-center gap-2 text-lg font-semibold text-slate-900">
          {inProgress && <Spinner />}
          {headline}
        </p>
        <p className="text-sm text-slate-600">
          Query from {latest.insurer_name ?? "the insurer"}
          {latest.letter_date ? ` dated ${latest.letter_date}` : ""} · received {formatDateTime(latest.created_at)}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {notifications.map((n) => (
          <div key={n.id} className="rounded-lg bg-white px-3 py-2 text-xs shadow-sm ring-1 ring-slate-200">
            <p className="font-semibold text-slate-800">{n.channel === "whatsapp" ? "WhatsApp" : "Email"}</p>
            <div className="mt-1 flex items-center gap-2">
              <StatusBadge map={NOTIFICATION_STATUS} code={n.status} />
              <span className="text-slate-500">{formatDateTime(n.delivered_at ?? n.sent_at)}</span>
            </div>
          </div>
        ))}
        <Link to={`/letters/${latest.id}`}>
          <Button variant={latest.status === "awaiting_review" ? "primary" : "secondary"}>
            {latest.status === "awaiting_review" ? "Review now" : "Open letter"}
          </Button>
        </Link>
      </div>
    </section>
  );
}

function Messages({ patientId }: { patientId: string }) {
  const messages = useQuery({
    queryKey: ["patient-messages", patientId],
    queryFn: () => api<PatientMessageRow[]>(`/patients/${patientId}/messages`),
    refetchInterval: 5000,
  });
  const rows = messages.data?.filter((row) => !row.message.superseded_at) ?? [];

  return (
    <Card title="Messages to the patient">
      {messages.isLoading ? (
        <Spinner label="Loading…" />
      ) : !rows.length ? (
        <Empty>No messages yet. A message is prepared and sent once a query letter has been checked.</Empty>
      ) : (
        <div className="space-y-6">
          {rows.map((row) => (
            <div key={row.message.id}>
              <p className="mb-2 text-sm font-medium">
                For the query from {row.insurer_name ?? "the insurer"}
                {row.letter_date ? ` dated ${row.letter_date}` : ""} ·{" "}
                <Link to={`/letters/${row.letter_id}`} className="text-sky-700 hover:underline">
                  open letter
                </Link>
              </p>
              <MessageView message={row.message} />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

const DETAIL_FIELDS = [
  ["full_name", "Full name", "text"],
  ["phone", "Mobile", "tel"],
  ["email", "Email", "email"],
  ["date_of_birth", "Date of birth", "date"],
  ["policy_number", "Policy number", "text"],
  ["claim_number", "Claim number", "text"],
] as const;

function PatientDetails({ patient }: { patient: Patient }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => {
      const changes: Record<string, string | null> = {};
      for (const [name] of DETAIL_FIELDS) {
        const before = (patient[name] ?? "") as string;
        const after = (form[name] ?? "").trim();
        if (after !== before) changes[name] = after === "" ? null : after;
      }
      return api<Patient>(`/patients/${patient.id}`, { method: "PATCH", body: changes });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["patient", patient.id], updated);
      queryClient.invalidateQueries({ queryKey: ["patients"] });
      setEditing(false);
    },
  });

  function startEditing() {
    setForm(Object.fromEntries(DETAIL_FIELDS.map(([name]) => [name, (patient[name] ?? "") as string])));
    save.reset();
    setEditing(true);
  }

  return (
    <Card
      title="Patient details"
      actions={!editing && <Button variant="secondary" onClick={startEditing}>Edit</Button>}
    >
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="grid gap-4 sm:grid-cols-3"
        >
          {DETAIL_FIELDS.map(([name, label, type]) => (
            <Labelled key={name} label={label}>
              <input
                type={type}
                required={["full_name", "phone", "email"].includes(name)}
                className={inputClass}
                value={form[name] ?? ""}
                onChange={(e) => setForm({ ...form, [name]: e.target.value })}
              />
            </Labelled>
          ))}
          <div className="flex gap-2 sm:col-span-3">
            <Button type="submit" disabled={save.isPending}>Save changes</Button>
            <Button type="button" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
          <div className="sm:col-span-3">
            <ErrorBox error={save.error} />
          </div>
        </form>
      ) : (
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
          {[["Patient ID", patient.patient_code], ...DETAIL_FIELDS.map(([name, label]) => [label, (patient[name] ?? "—") as string])].map(
            ([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-slate-500 uppercase">{label}</dt>
                <dd className="font-medium break-all">{value}</dd>
              </div>
            ),
          )}
        </dl>
      )}
    </Card>
  );
}

interface Assignment {
  user_id: string;
  full_name: string;
  role: string;
  assigned_at: string;
}

function Assignments({ patientId }: { patientId: string }) {
  const queryClient = useQueryClient();
  const canAssign = usePermission("patients:assign");
  const [editing, setEditing] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const assigned = useQuery({
    queryKey: ["assignments", patientId],
    queryFn: () => api<Assignment[]>(`/patients/${patientId}/assignments`),
  });
  const staff = useQuery({
    queryKey: ["staff-directory"],
    queryFn: () => api<{ id: string; full_name: string; role: string }[]>("/staff-directory"),
    enabled: canAssign && editing,
  });
  const save = useMutation({
    mutationFn: () =>
      api(`/patients/${patientId}/assignments`, { method: "PUT", body: { user_ids: [...chosen] } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assignments", patientId] });
      setEditing(false);
    },
  });

  return (
    <Card
      title="Assigned staff"
      actions={
        canAssign &&
        !editing && (
          <Button
            variant="secondary"
            onClick={() => {
              setChosen(new Set(assigned.data?.map((a) => a.user_id)));
              setEditing(true);
            }}
          >
            Change
          </Button>
        )
      }
    >
      {editing ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-500">
            Staff can only see patients assigned to them. Case managers and administrators see everyone.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            {staff.data?.map((person) => (
              <label key={person.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={chosen.has(person.id)}
                  onChange={(e) => {
                    const next = new Set(chosen);
                    if (e.target.checked) next.add(person.id);
                    else next.delete(person.id);
                    setChosen(next);
                  }}
                />
                {person.full_name} <span className="text-xs text-slate-500">{person.role.replace("_", " ")}</span>
              </label>
            ))}
          </div>
          <ErrorBox error={save.error} />
          <div className="flex gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : !assigned.data?.length ? (
        <p className="text-sm text-slate-500">No staff assigned; only case managers and administrators can see this patient.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {assigned.data.map((a) => (
            <li key={a.user_id}>
              <Badge tone="blue">{a.full_name}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function PatientPage() {
  const { patientId = "" } = useParams();
  const [params] = useSearchParams();
  const patient = useQuery({
    queryKey: ["patient", patientId],
    queryFn: () => api<Patient>(`/patients/${patientId}`),
  });

  if (patient.isLoading) return <Spinner label="Loading patient…" />;
  if (patient.error || !patient.data) return <ErrorBox error={patient.error ?? "Patient not found."} />;
  const p = patient.data;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/patients" className="text-sm text-sky-700 hover:underline">
          ← Patients
        </Link>
        <div className="mt-1 flex flex-wrap items-baseline gap-3">
          <h1 className="text-2xl font-semibold">{p.full_name}</h1>
          <span className="rounded-md bg-slate-900 px-2 py-0.5 font-mono text-sm text-white">{p.patient_code}</span>
        </div>
      </div>
      {params.get("created") && (
        <Notice tone="green">
          Patient created with Patient ID <strong>{p.patient_code}</strong>. Next, upload their existing documents.
        </Notice>
      )}

      <CaseSummary patientId={patientId} />
      <QueryLetters patientId={patientId} />
      <Locker patientId={patientId} />
      <Messages patientId={patientId} />

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <PatientDetails patient={p} />
        <Assignments patientId={patientId} />
      </div>

      <p className="text-center text-xs text-slate-500">
        Files are encrypted when stored. Every view and download of this patient's documents is recorded in the
        audit log. Patient messages never name a diagnosis or test, because phone previews can be seen by others.
      </p>
    </div>
  );
}
