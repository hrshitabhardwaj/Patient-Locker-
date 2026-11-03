import { formatDateTime, NOTIFICATION_STATUS } from "../lib/labels";
import type { Notification, PatientMessage } from "../lib/types";
import { Badge, Notice, StatusBadge } from "./ui";

const CHANNEL_LABEL = { whatsapp: "WhatsApp", email: "Email" };

function Delivery({ notification }: { notification: Notification }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{CHANNEL_LABEL[notification.channel]}</span>
        <StatusBadge map={NOTIFICATION_STATUS} code={notification.status} />
      </div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-slate-600">
        <dt>To</dt>
        <dd className="break-all">{notification.recipient}</dd>
        <dt>Sent</dt>
        <dd>{formatDateTime(notification.sent_at)}</dd>
        <dt>Delivered</dt>
        <dd>{formatDateTime(notification.delivered_at)}</dd>
        <dt>Provider</dt>
        <dd>
          {notification.provider}
          {notification.attempts > 1 ? ` · ${notification.attempts} attempts` : ""}
        </dd>
      </dl>
      {notification.last_error && (
        <p className="mt-2 text-xs text-red-700">Last error: {notification.last_error}</p>
      )}
      <details className="mt-2 text-xs text-slate-500">
        <summary className="cursor-pointer">History</summary>
        <ol className="mt-1 space-y-1">
          {notification.events.map((event, i) => (
            <li key={i}>
              {formatDateTime(event.occurred_at)} — <strong>{event.status}</strong>
              {event.detail ? `: ${event.detail}` : ""}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

export default function MessageView({ message }: { message: PatientMessage }) {
  const usesTestProvider = message.notifications.some((n) => n.provider === "test-outbox");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={message.kind === "documents_needed" ? "violet" : "green"}>
          {message.kind === "documents_needed" ? "Documents needed" : "Nothing needed"}
        </Badge>
        {message.status === "blocked" && <Badge tone="red">Blocked</Badge>}
        {message.superseded_at && <Badge>Replaced by a newer version</Badge>}
        <span className="text-slate-500">Prepared {formatDateTime(message.created_at)}</span>
      </div>

      {message.status === "blocked" ? (
        <Notice tone="red">
          This message cannot be sent because a value is missing:{" "}
          {message.blocked_reasons.map((r) => r.replace("missing:", "")).join(", ")}. Set the value
          (company details in the settings, or the letter's insurer and claim number), then match
          the letter again.
        </Notice>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <p className="mb-1 text-xs font-semibold text-slate-500 uppercase">WhatsApp</p>
            <div className="rounded-xl rounded-tl-sm bg-emerald-50 p-4 text-sm whitespace-pre-wrap text-slate-800 ring-1 ring-emerald-100">
              {message.whatsapp_text}
            </div>
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-slate-500 uppercase">Email</p>
            <div className="rounded-xl border border-slate-200 bg-white text-sm">
              <p className="border-b border-slate-100 px-4 py-2 font-medium">
                Subject: {message.email_subject}
              </p>
              <p className="px-4 py-3 whitespace-pre-wrap text-slate-800">{message.email_text}</p>
            </div>
          </div>
        </div>
      )}

      {message.notifications.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold text-slate-500 uppercase">Delivery</p>
          {usesTestProvider && (
            <div className="mb-2">
              <Notice tone="amber">
                Test provider: recorded in the local outbox. No real WhatsApp or email was sent;
                live delivery is connected once company accounts exist.
              </Notice>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            {message.notifications.map((n) => (
              <Delivery key={n.id} notification={n} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
