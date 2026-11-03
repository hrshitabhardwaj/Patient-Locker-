import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Card, Empty, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime, LETTER_STATUS, OUTCOME } from "../lib/labels";
import type { DashboardStats, LetterRow } from "../lib/types";

const TILES: { key: keyof DashboardStats; label: string; to?: string; tone?: string }[] = [
  { key: "total_patients", label: "Patients", to: "/patients" },
  { key: "total_documents", label: "Documents in lockers" },
  { key: "total_query_letters", label: "Query letters" },
  { key: "letters_processing", label: "Being processed" },
  { key: "letters_awaiting_review", label: "Awaiting staff review", to: "/review", tone: "text-amber-700" },
  { key: "letters_completed", label: "Completed (patient notified)", tone: "text-emerald-700" },
  { key: "patients_with_missing_documents", label: "Patients with missing documents" },
  { key: "letters_failed", label: "Letters that failed", tone: "text-red-700" },
];

export default function DashboardPage() {
  const stats = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: () => api<DashboardStats>("/dashboard/stats"),
    refetchInterval: 5000,
  });
  const letters = useQuery({
    queryKey: ["dashboard-letters"],
    queryFn: () => api<LetterRow[]>("/dashboard/letters?limit=10"),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <ErrorBox error={stats.error} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {TILES.map((tile) => {
          const value = stats.data?.[tile.key];
          const body = (
            <div className="h-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-sky-300">
              <p className="text-sm text-slate-500">{tile.label}</p>
              <p className={`mt-1 text-3xl font-semibold ${tile.tone ?? "text-slate-900"}`}>
                {value ?? "–"}
              </p>
            </div>
          );
          return tile.to ? (
            <Link key={tile.key} to={tile.to}>
              {body}
            </Link>
          ) : (
            <div key={tile.key}>{body}</div>
          );
        })}
      </div>

      <Card title="Recent query letters">
        {letters.isLoading ? (
          <Spinner label="Loading…" />
        ) : !letters.data?.length ? (
          <Empty>
            No query letters yet. Open a patient and upload the insurer's letter to start.
          </Empty>
        ) : (
          <LetterTable rows={letters.data} />
        )}
      </Card>
    </div>
  );
}

export function LetterTable({ rows }: { rows: LetterRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-slate-500 uppercase">
          <tr>
            <th className="py-2 pr-4">Patient</th>
            <th className="py-2 pr-4">Insurer / claim</th>
            <th className="py-2 pr-4">Status</th>
            <th className="py-2 pr-4">Outcome</th>
            <th className="py-2">Received</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={row.id} className="hover:bg-slate-50">
              <td className="py-2 pr-4">
                <Link to={`/letters/${row.id}`} className="font-medium text-sky-800 hover:underline">
                  {row.patient_name}
                </Link>
                <span className="block text-xs text-slate-500">{row.patient_code}</span>
              </td>
              <td className="py-2 pr-4">
                {row.insurer_name ?? "—"}
                <span className="block text-xs text-slate-500">{row.claim_reference ?? ""}</span>
              </td>
              <td className="py-2 pr-4">
                <StatusBadge map={LETTER_STATUS} code={row.status} />
              </td>
              <td className="py-2 pr-4">
                <StatusBadge map={OUTCOME} code={row.outcome} />
              </td>
              <td className="py-2 text-slate-500">{formatDateTime(row.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
