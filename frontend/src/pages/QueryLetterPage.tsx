import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import MessageView from "../components/MessageView";
import {
  Badge,
  Button,
  Card,
  ErrorBox,
  inputClass,
  Labelled,
  Notice,
  Spinner,
  StatusBadge,
} from "../components/ui";
import { api } from "../lib/api";
import {
  categoryLabel,
  formatDateTime,
  LETTER_IN_PROGRESS,
  LETTER_STATUS,
  MATCH_STATUS,
  OUTCOME,
  REVIEW_REASON,
} from "../lib/labels";
import type { LockerDocument, QueryLetterDetail, QueryPoint } from "../lib/types";

const OUTCOME_EXPLAINED: Record<string, [string, "violet" | "amber" | "green"]> = {
  send_missing_items_message: [
    "The patient is asked only for the items marked NEEDS_FROM_PATIENT. Covered items are not requested again.",
    "violet",
  ],
  hold_for_staff_review: [
    "Held: nothing is sent to the patient until every NEEDS_REVIEW point below has a staff decision. Sending then happens automatically.",
    "amber",
  ],
  no_additional_documents_required: [
    "Every requirement is already covered by the locker. The patient is told no further documents are needed.",
    "green",
  ],
};

function ReviewForm({ point, letter }: { point: QueryPoint; letter: QueryLetterDetail }) {
  const queryClient = useQueryClient();
  const [decision, setDecision] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [note, setNote] = useState("");
  const documents = useQuery({
    queryKey: ["documents", letter.patient_id],
    queryFn: () => api<LockerDocument[]>(`/patients/${letter.patient_id}/documents`),
  });
  const review = useMutation({
    mutationFn: () =>
      api<QueryLetterDetail>(`/query-points/${point.id}/review`, {
        method: "POST",
        body: { decision, note, document_id: decision === "covered" ? documentId : undefined },
      }),
    onSuccess: (updated) => queryClient.setQueryData(["letter", letter.id], updated),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    review.mutate();
  }

  const supporting = documents.data?.filter((d) => d.kind === "supporting" && d.processing_status !== "failed") ?? [];

  return (
    <form onSubmit={submit} className="mt-3 space-y-3 rounded-lg bg-amber-50 p-3 ring-1 ring-amber-200">
      <p className="text-sm font-semibold text-amber-900">Staff decision</p>
      <div className="flex flex-wrap gap-4 text-sm">
        {[
          ["covered", "Covered by a locker document"],
          ["needs_from_patient", "Ask the patient for it"],
          ["not_a_requirement", "Not a requirement"],
        ].map(([value, label]) => (
          <label key={value} className="flex items-center gap-1.5">
            <input type="radio" name={`decision-${point.id}`} value={value} checked={decision === value} onChange={() => setDecision(value)} />
            {label}
          </label>
        ))}
      </div>
      {decision === "covered" && (
        <select required value={documentId} onChange={(e) => setDocumentId(e.target.value)} className={inputClass}>
          <option value="">Choose the document…</option>
          {supporting.map((d) => (
            <option key={d.id} value={d.id}>
              {d.original_filename} ({categoryLabel(d.declared_category ?? d.detected_category)})
            </option>
          ))}
        </select>
      )}
      <textarea
        required
        minLength={3}
        placeholder="Reason for the decision (kept in the audit log)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className={inputClass}
        rows={2}
      />
      <ErrorBox error={review.error} />
      <Button type="submit" disabled={!decision || review.isPending}>
        {review.isPending ? "Saving…" : "Save decision"}
      </Button>
    </form>
  );
}

function PointCard({ point, letter }: { point: QueryPoint; letter: QueryLetterDetail }) {
  const match = point.current_match;
  const [reviewing, setReviewing] = useState(false);
  const dismissed = Boolean(point.dismissed_at);

  return (
    <li className={`rounded-xl border bg-white p-4 shadow-sm ${dismissed ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-slate-500 uppercase">
            Point {point.seq} · {categoryLabel(point.requirement_type)}
          </p>
          <p className="mt-0.5 font-semibold text-slate-900">{point.requirement.split(" — ")[0]}</p>
          {point.conditions.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {point.conditions.map((c) => (
                <Badge key={c.code}>{c.text}</Badge>
              ))}
            </div>
          )}
        </div>
        {dismissed ? (
          <Badge>Not a requirement</Badge>
        ) : (
          <StatusBadge map={MATCH_STATUS} code={match?.status ?? (LETTER_IN_PROGRESS.has(letter.status) ? null : "needs_review")} />
        )}
      </div>

      <blockquote className="mt-3 border-l-2 border-slate-200 pl-3 text-sm text-slate-500 italic">
        “{point.source_text}”
      </blockquote>

      {match && !dismissed && (
        <div className="mt-3 space-y-2 text-sm">
          <p className="text-slate-800">
            {match.explanation}
            {match.decided_by === "staff" && <Badge tone="blue">Staff decision</Badge>}
          </p>
          {match.evidence.length > 0 && (
            <ul className="space-y-1">
              {match.evidence.map((e, i) => (
                <li key={i} className="rounded-md bg-slate-50 px-3 py-2 text-xs">
                  <a
                    href={`/api/v1/documents/${e.document_id}/content`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-semibold text-sky-800 hover:underline"
                  >
                    {e.filename}
                  </a>
                  {e.page ? ` · page ${e.page}` : ""}
                  {e.kind ? ` · ${e.kind} seen on the page image` : ""}
                  {e.quote && <span className="block text-slate-600">“{e.quote}”</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {match?.status === "needs_review" && !dismissed && (
        reviewing ? (
          <ReviewForm point={point} letter={letter} />
        ) : (
          <Button className="mt-3" variant="secondary" onClick={() => setReviewing(true)}>
            Resolve this point
          </Button>
        )
      )}
    </li>
  );
}

function ConfirmLetter({ letter }: { letter: QueryLetterDetail }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const confirm = useMutation({
    mutationFn: () =>
      api<QueryLetterDetail>(`/query-letters/${letter.id}/confirm`, { method: "POST", body: { note } }),
    onSuccess: (updated) => queryClient.setQueryData(["letter", letter.id], updated),
  });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        confirm.mutate();
      }}
      className="mt-3 flex flex-wrap gap-2"
    >
      <input
        required
        minLength={3}
        placeholder="What did you check? (kept in the audit log)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className={`${inputClass} min-w-64 flex-1`}
      />
      <Button type="submit" disabled={confirm.isPending}>
        Confirm the letter belongs to this patient
      </Button>
      <ErrorBox error={confirm.error} />
    </form>
  );
}

function LetterDetails({ letter }: { letter: QueryLetterDetail }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ insurer_name: "", claim_reference: "", response_days: "" });
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, string | number> = {};
      if (form.insurer_name.trim() && form.insurer_name !== (letter.insurer_name ?? "")) body.insurer_name = form.insurer_name.trim();
      if (form.claim_reference.trim() && form.claim_reference !== (letter.claim_reference ?? "")) body.claim_reference = form.claim_reference.trim();
      if (form.response_days && Number(form.response_days) !== letter.response_days) body.response_days = Number(form.response_days);
      return api<QueryLetterDetail>(`/query-letters/${letter.id}`, { method: "PATCH", body });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["letter", letter.id], updated);
      setEditing(false);
    },
  });
  const rematch = useMutation({
    mutationFn: () => api(`/query-letters/${letter.id}/rematch`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["letter", letter.id] }),
  });

  return (
    <Card
      title="Letter details used in the message"
      actions={
        <div className="flex gap-2">
          <Button
            variant="secondary"
            disabled={rematch.isPending}
            onClick={() => rematch.mutate()}
            title="Check the requirements against the locker again, e.g. after new documents arrive"
          >
            {rematch.isPending ? "Queued…" : "Match again"}
          </Button>
          {!editing && (
            <Button
              variant="secondary"
              onClick={() => {
                setForm({
                  insurer_name: letter.insurer_name ?? "",
                  claim_reference: letter.claim_reference ?? "",
                  response_days: letter.response_days ? String(letter.response_days) : "",
                });
                setEditing(true);
              }}
            >
              Edit
            </Button>
          )}
        </div>
      }
    >
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
          className="grid gap-4 sm:grid-cols-3"
        >
          <Labelled label="Insurer / TPA name">
            <input className={inputClass} value={form.insurer_name} onChange={(e) => setForm({ ...form, insurer_name: e.target.value })} />
          </Labelled>
          <Labelled label="Claim number">
            <input className={inputClass} value={form.claim_reference} onChange={(e) => setForm({ ...form, claim_reference: e.target.value })} />
          </Labelled>
          <Labelled label="Deadline (days from the letter date)" hint="Leave empty if the letter sets none.">
            <input type="number" min={1} max={90} className={inputClass} value={form.response_days} onChange={(e) => setForm({ ...form, response_days: e.target.value })} />
          </Labelled>
          <div className="flex gap-2 sm:col-span-3">
            <Button type="submit" disabled={save.isPending}>Save and prepare the message again</Button>
            <Button type="button" variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
          <div className="sm:col-span-3">
            <ErrorBox error={save.error} />
          </div>
        </form>
      ) : (
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          {[
            ["Insurer / TPA", letter.insurer_name ?? "— (not found on the letterhead)"],
            ["Claim number", letter.claim_reference ?? "—"],
            ["Letter date", letter.letter_date ?? "—"],
            ["Deadline", letter.response_days ? `${letter.response_days} days` : "none stated"],
          ].map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-slate-500 uppercase">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <ErrorBox error={rematch.error} />
    </Card>
  );
}

export default function QueryLetterPage() {
  const { letterId = "" } = useParams();
  const letter = useQuery({
    queryKey: ["letter", letterId],
    queryFn: () => api<QueryLetterDetail>(`/query-letters/${letterId}`),
    refetchInterval: (query) => (query.state.data && LETTER_IN_PROGRESS.has(query.state.data.status) ? 2000 : 10000),
  });

  if (letter.isLoading) return <Spinner label="Loading query letter…" />;
  if (letter.error || !letter.data) return <ErrorBox error={letter.error ?? "Query letter not found."} />;
  const l = letter.data;
  const inProgress = LETTER_IN_PROGRESS.has(l.status) && !l.outcome;
  const letterLevel = l.review_reasons.filter((r) => ["identity_mismatch", "no_requirements_found", "letter_unreadable"].includes(r));
  const counts = l.points.reduce<Record<string, number>>((acc, p) => {
    const status = p.dismissed_at ? "dismissed" : p.current_match?.status ?? "pending";
    acc[status] = (acc[status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div>
        <Link to={`/patients/${l.patient_id}`} className="text-sm text-sky-700 hover:underline">
          ← Patient profile
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">Query from {l.insurer_name ?? "insurer"}</h1>
          <StatusBadge map={LETTER_STATUS} code={l.status} />
          <StatusBadge map={OUTCOME} code={l.outcome} />
        </div>
        <p className="mt-1 text-sm text-slate-500">
          {l.claim_reference ? `Claim ${l.claim_reference} · ` : ""}
          {l.letter_date ? `Letter dated ${l.letter_date} · ` : ""}
          Uploaded {formatDateTime(l.created_at)} ·{" "}
          <a href={`/api/v1/documents/${l.document_id}/content`} target="_blank" rel="noreferrer" className="text-sky-700 hover:underline">
            view letter
          </a>
        </p>
      </div>

      {inProgress && (
        <Notice>
          <Spinner label="Reading the letter and checking the patient's documents. This page updates by itself." />
        </Notice>
      )}
      {l.error && <Notice tone="red">Processing failed: {l.error.replaceAll("_", " ")}.</Notice>}
      {l.outcome && (
        <Notice tone={OUTCOME_EXPLAINED[l.outcome][1]}>{OUTCOME_EXPLAINED[l.outcome][0]}</Notice>
      )}

      {l.review_reasons.length > 0 && (
        <Card title="Needs attention">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {l.review_reasons.map((r) => (
              <li key={r}>{REVIEW_REASON[r] ?? r}</li>
            ))}
          </ul>
          {Object.keys(l.identity_details).length > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              Identity checks:{" "}
              {Object.entries(l.identity_details)
                .map(([field, v]) => `${field.replaceAll("_", " ")}: ${v.result.replaceAll("_", " ")}`)
                .join(" · ")}
            </p>
          )}
          {letterLevel.length > 0 && <ConfirmLetter letter={l} />}
        </Card>
      )}

      <Card
        title={`Query points (${l.points.length})`}
        actions={
          <div className="flex flex-wrap gap-1">
            {counts.covered ? <Badge tone="green">{counts.covered} covered</Badge> : null}
            {counts.needs_from_patient ? <Badge tone="violet">{counts.needs_from_patient} from patient</Badge> : null}
            {counts.needs_review ? <Badge tone="amber">{counts.needs_review} need review</Badge> : null}
          </div>
        }
      >
        {l.points.length === 0 ? (
          inProgress ? <Spinner label="Reading the letter…" /> : <p className="text-sm text-slate-500">No points.</p>
        ) : (
          <ol className="space-y-3">
            {l.points.map((point) => (
              <PointCard key={point.id} point={point} letter={l} />
            ))}
          </ol>
        )}
      </Card>

      <LetterDetails letter={l} />

      <Card title="Message to the patient">
        {l.current_message ? (
          <MessageView message={l.current_message} />
        ) : (
          <p className="text-sm text-slate-500">
            {l.outcome === "hold_for_staff_review"
              ? "No message while points need review."
              : "The message is prepared once checking is complete."}
          </p>
        )}
      </Card>

      {l.extraction_details.segments && (
        <Card title="How the letter was read">
          <ul className="space-y-1 text-sm">
            {l.extraction_details.segments.map((s) => (
              <li key={s.index} className={s.used ? "text-slate-800" : "text-slate-400"}>
                <Badge tone={s.used ? "blue" : "grey"}>{s.used ? "Used" : "Skipped"}</Badge>{" "}
                {s.text} <span className="text-xs">({s.reason})</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
