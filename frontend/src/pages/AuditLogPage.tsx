import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge, Button, Card, ErrorBox, inputClass, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/labels";
import type { Page } from "../lib/types";

interface AuditEntry {
  id: number;
  occurred_at: string;
  actor_user_id: string | null;
  action: string;
  outcome: string;
  entity_type: string | null;
  entity_id: string | null;
  patient_id: string | null;
  ip_address: string | null;
  details: Record<string, unknown>;
}

const PAGE_SIZE = 50;
const OUTCOME_TONE = { success: "green", failure: "red", denied: "amber" } as const;

export default function AuditLogPage() {
  const [action, setAction] = useState("");
  const [outcome, setOutcome] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (action.trim()) params.set("action", action.trim());
  if (outcome) params.set("outcome", outcome);

  const entries = useQuery({
    queryKey: ["audit", action, outcome, offset],
    queryFn: () => api<Page<AuditEntry>>(`/audit-logs?${params}`),
    placeholderData: keepPreviousData,
  });
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api<Page<{ id: string; full_name: string }>>("/users?limit=200"),
  });
  const names = new Map(users.data?.items.map((u) => [u.id, u.full_name]));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Audit log</h1>
        <p className="text-sm text-slate-500">
          Every sign-in, view, upload, AI decision, staff decision and message attempt. Entries cannot be
          changed or deleted; viewing this page is itself recorded.
        </p>
      </div>

      <Card>
        <div className="mb-4 flex flex-wrap gap-3">
          <input
            placeholder="Action, e.g. query_letter.matched"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
              setOffset(0);
            }}
            className={`${inputClass} max-w-sm`}
          />
          <select
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value);
              setOffset(0);
            }}
            className={`${inputClass} max-w-48`}
          >
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
            <option value="denied">Denied</option>
          </select>
        </div>
        <ErrorBox error={entries.error} />
        {entries.isLoading ? (
          <Spinner label="Loading…" />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-slate-500 uppercase">
                  <tr>
                    <th className="py-2 pr-4">When</th>
                    <th className="py-2 pr-4">Who</th>
                    <th className="py-2 pr-4">Action</th>
                    <th className="py-2 pr-4">Outcome</th>
                    <th className="py-2">Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 align-top">
                  {entries.data?.items.map((entry) => (
                    <tr key={entry.id}>
                      <td className="py-2 pr-4 whitespace-nowrap text-slate-500">{formatDateTime(entry.occurred_at)}</td>
                      <td className="py-2 pr-4">
                        {entry.actor_user_id ? (names.get(entry.actor_user_id) ?? "Staff member") : <Badge>System</Badge>}
                        {entry.ip_address && <span className="block text-xs text-slate-400">{entry.ip_address}</span>}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {entry.action}
                        {entry.patient_id && (
                          <Link to={`/patients/${entry.patient_id}`} className="block font-sans text-sky-700 hover:underline">
                            patient
                          </Link>
                        )}
                      </td>
                      <td className="py-2 pr-4">
                        <Badge tone={OUTCOME_TONE[entry.outcome as keyof typeof OUTCOME_TONE] ?? "grey"}>{entry.outcome}</Badge>
                      </td>
                      <td className="max-w-md py-2 font-mono text-xs break-all text-slate-600">
                        {Object.keys(entry.details).length ? JSON.stringify(entry.details) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
              <span>
                {entries.data?.total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, entries.data.total)} of ${entries.data.total}` : "No entries"}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Newer
                </Button>
                <Button
                  variant="secondary"
                  disabled={!entries.data || offset + PAGE_SIZE >= entries.data.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Older
                </Button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
