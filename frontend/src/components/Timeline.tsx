import { formatDuration, formatTime, LETTER_IN_PROGRESS, secondsBetween } from "../lib/labels";
import type { Notification, QueryLetterDetail } from "../lib/types";
import { Spinner } from "./ui";

type State = "done" | "active" | "waiting" | "failed" | "todo";

interface Step {
  title: string;
  detail?: string;
  at?: string | null;
  state: State;
}

const DOT: Record<State, string> = {
  done: "bg-emerald-600 text-white",
  active: "bg-sky-600 text-white",
  waiting: "bg-amber-500 text-white",
  failed: "bg-red-600 text-white",
  todo: "bg-slate-200 text-slate-500",
};

const SYMBOL: Record<State, string> = { done: "✓", active: "…", waiting: "!", failed: "×", todo: "" };

function deliveryStep(label: string, notification: Notification | undefined, held: boolean): Step {
  if (!notification) return { title: label, detail: held ? "after the staff decision" : undefined, state: "todo" };
  if (notification.status === "failed") {
    return { title: label, detail: "failed", at: notification.failed_at, state: "failed" };
  }
  if (notification.status === "delivered" || notification.status === "read") {
    return { title: label, detail: "delivered", at: notification.delivered_at ?? notification.sent_at, state: "done" };
  }
  if (notification.status === "sent") return { title: label, detail: "sent", at: notification.sent_at, state: "done" };
  return { title: label, detail: "sending…", state: "active" };
}

/** What the system did with a letter, step by step, with times. */
export default function Timeline({ letter }: { letter: QueryLetterDetail }) {
  const message = letter.current_message;
  const notifications = message?.notifications ?? [];
  const whatsapp = notifications.find((n) => n.channel === "whatsapp");
  const email = notifications.find((n) => n.channel === "email");
  const inProgress = LETTER_IN_PROGRESS.has(letter.status);
  const held = letter.status === "awaiting_review";
  const live = letter.points.filter((p) => !p.dismissed_at);
  const count = (status: string) => live.filter((p) => p.current_match?.status === status).length;
  const staffDecided = live.some((p) => p.current_match?.decided_by === "staff");

  const steps: Step[] = [
    { title: "Letter uploaded", at: letter.created_at, state: "done" },
    letter.extracted_at
      ? { title: "Letter read", detail: `${letter.point_count} point(s) found`, at: letter.extracted_at, state: "done" }
      : { title: "Reading the letter", state: letter.error ? "failed" : "active" },
    letter.matched_at
      ? {
          title: "Checked against the locker",
          detail: [
            count("covered") && `${count("covered")} already in locker`,
            count("needs_from_patient") && `${count("needs_from_patient")} to ask`,
            count("needs_review") && `${count("needs_review")} for staff`,
          ]
            .filter(Boolean)
            .join(" · "),
          at: letter.matched_at,
          state: "done",
        }
      : { title: "Checking the locker", state: letter.extracted_at && inProgress ? "active" : "todo" },
  ];
  if (held) {
    steps.push({
      title: "Waiting for staff",
      detail: count("needs_review")
        ? `${count("needs_review")} point(s) to check before anything is sent`
        : "a check before anything is sent",
      state: "waiting",
    });
  }
  steps.push(
    message
      ? {
          title: "Message prepared",
          detail: message.kind === "documents_needed" ? "documents needed" : "nothing needed",
          at: message.created_at,
          state: message.status === "blocked" ? "failed" : "done",
        }
      : { title: "Message prepared", state: letter.matched_at && inProgress ? "active" : "todo" },
    deliveryStep("WhatsApp", whatsapp, held),
    deliveryStep("Email", email, held),
  );

  const sentTimes = notifications.map((n) => n.sent_at).filter((t): t is string => Boolean(t));
  const firstSent = sentTimes.sort()[0];
  const total = firstSent ? secondsBetween(letter.created_at, firstSent) : null;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">What happened automatically</h2>
        {total !== null ? (
          <p className="text-sm">
            <span className="text-slate-500">Upload to message: </span>
            <span className="text-lg font-semibold text-emerald-700">{formatDuration(total)}</span>
            <span className="ml-2 text-xs text-slate-500">
              {staffDecided ? "includes a staff decision" : "no staff action needed"}
            </span>
          </p>
        ) : inProgress ? (
          <Spinner label="Working…" />
        ) : null}
      </div>
      <ol className="grid gap-3 sm:grid-cols-2 lg:flex lg:gap-0">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-3 lg:flex-1 lg:flex-col lg:gap-2">
            <div className="flex items-center lg:w-full">
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold ${DOT[step.state]} ${step.state === "active" ? "animate-pulse" : ""}`}
              >
                {SYMBOL[step.state]}
              </span>
              {i < steps.length - 1 && (
                <span
                  className={`hidden h-0.5 flex-1 lg:block ${step.state === "done" ? "bg-emerald-300" : "bg-slate-200"}`}
                />
              )}
            </div>
            <div className="min-w-0 lg:pr-3">
              <p className={`text-sm font-medium ${step.state === "todo" ? "text-slate-400" : "text-slate-900"}`}>
                {step.title}
              </p>
              {step.detail && <p className="text-xs text-slate-500">{step.detail}</p>}
              {step.at && (
                <p className="text-xs text-slate-500">
                  {formatTime(step.at)}
                  {i > 0 && (
                    <span className="ml-1 text-slate-400">+{formatDuration(secondsBetween(letter.created_at, step.at))}</span>
                  )}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
