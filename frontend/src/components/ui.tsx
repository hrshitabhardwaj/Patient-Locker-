import type { ButtonHTMLAttributes, ReactNode } from "react";

import type { Tone } from "../lib/labels";

const TONES: Record<Tone, string> = {
  grey: "bg-slate-100 text-slate-700 ring-slate-200",
  blue: "bg-sky-50 text-sky-800 ring-sky-200",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  amber: "bg-amber-50 text-amber-900 ring-amber-200",
  red: "bg-red-50 text-red-800 ring-red-200",
  violet: "bg-violet-50 text-violet-800 ring-violet-200",
};

export function Badge({ tone = "grey", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap ring-1 ring-inset ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ map, code }: { map: Record<string, [string, Tone]>; code: string | null }) {
  if (!code) return <span className="text-slate-400">—</span>;
  const [label, tone] = map[code] ?? [code, "grey"];
  return <Badge tone={tone}>{label}</Badge>;
}

export function Card({
  title,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" }) {
  const styles = {
    primary: "bg-sky-700 text-white hover:bg-sky-800 disabled:bg-sky-300",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:opacity-50",
    ghost: "text-sky-700 hover:bg-sky-50 disabled:opacity-50",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-semibold transition ${styles} ${className}`}
    />
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-500">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-sky-700" />
      {label}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg bg-slate-50 px-4 py-6 text-center text-sm text-slate-500">{children}</p>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}

export function Notice({ tone = "blue", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={`rounded-lg px-4 py-3 text-sm ring-1 ring-inset ${TONES[tone]}`}>{children}</div>;
}

export const inputClass =
  "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-600/20 focus:outline-none";

export function Labelled({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}
