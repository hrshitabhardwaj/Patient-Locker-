import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card, Empty, ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime, formatDuration, LETTER_STATUS, NOTIFICATION_STATUS, OUTCOME } from "../lib/labels";
import type { DashboardStats, DeliveryRow, LetterRow } from "../lib/types";

const ALERT_TONE = {
  amber: "border-amber-300 bg-amber-50 text-amber-800",
  red: "border-red-300 bg-red-50 text-red-700",
  blue: "border-sky-300 bg-sky-50 text-sky-800",
};

function Tile({
  label,
  value,
  hint,
  to,
  alert,
  big,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  to?: string;
  alert?: keyof typeof ALERT_TONE | null;
  big?: boolean;
}) {
  const body = (
    <div
      className={`h-full rounded-xl border p-4 shadow-sm transition hover:border-sky-300 ${
        alert ? ALERT_TONE[alert] : "border-slate-200 bg-white text-slate-900"
      }`}
    >
      <p className={`text-sm ${alert ? "" : "text-slate-500"}`}>{label}</p>
      <p className={`mt-1 font-semibold ${big ? "text-4xl" : "text-3xl"}`}>{value ?? "–"}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
  return to ? <Link to={to}>{body}</Link> : body;
}

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
  const deliveries = useQuery({
    queryKey: ["dashboard-deliveries"],
    queryFn: () => api<DeliveryRow[]>("/dashboard/deliveries?limit=8"),
    refetchInterval: 5000,
  });
  const s = stats.data;
  const attention = s ? s.letters_awaiting_review + s.failed_deliveries + s.letters_failed : 0;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <ErrorBox error={stats.error} />

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">
          Needs attention{" "}
          {s && attention === 0 && <span className="font-normal text-emerald-700">· nothing waiting for you</span>}
        </h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile
            label="Letters waiting for staff"
            value={s?.letters_awaiting_review}
            to="/review"
            alert={s?.letters_awaiting_review ? "amber" : null}
          />
          <Tile label="Failed deliveries" value={s?.failed_deliveries} alert={s?.failed_deliveries ? "red" : null} />
          <Tile label="Letters that failed" value={s?.letters_failed} alert={s?.letters_failed ? "red" : null} />
          <Tile label="Being processed now" value={s?.letters_processing} alert={s?.letters_processing ? "blue" : null} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Automation</h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="col-span-2">
            <Tile
              big
              label="Typical time from letter upload to patient message"
              value={<span className="text-emerald-700">{s ? formatDuration(s.median_seconds_to_message) : "–"}</span>}
              hint="Median of letters handled with no staff step"
            />
          </div>
          <Tile label="Patients notified" value={s?.letters_completed} hint="letters completed" />
          <Tile label="Patients with documents to send" value={s?.patients_with_missing_documents} to="/patients" />
        </div>
      </section>

      <p className="text-sm text-slate-500">
        {s ? (
          <>
            <Link to="/patients" className="text-sky-700 hover:underline">
              {s.total_patients} patients
            </Link>{" "}
            · {s.total_documents} documents in lockers · {s.total_query_letters} query letters
          </>
        ) : (
          " "
        )}
      </p>

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card title="Recent query letters">
          {letters.isLoading ? (
            <Spinner label="Loading…" />
          ) : !letters.data?.length ? (
            <Empty>No query letters yet. Open a patient and upload the insurer's letter to start.</Empty>
          ) : (
            <LetterTable rows={letters.data} />
          )}
        </Card>
        <Card title="Recent messages sent">
          {deliveries.isLoading ? (
            <Spinner label="Loading…" />
          ) : !deliveries.data?.length ? (
            <Empty>No messages sent yet.</Empty>
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {deliveries.data.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <Link to={`/letters/${d.letter_id}`} className="font-medium text-sky-800 hover:underline">
                      {d.channel === "whatsapp" ? "WhatsApp" : "Email"} · {d.patient_code}
                    </Link>
                    <span className="block text-xs text-slate-500">{formatDateTime(d.sent_at ?? d.created_at)}</span>
                  </div>
                  <StatusBadge map={NOTIFICATION_STATUS} code={d.status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
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
