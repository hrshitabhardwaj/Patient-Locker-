// Thin wrapper over fetch: same-origin cookies, JSON, and the CSRF header on unsafe methods.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const CSRF_COOKIE = "pl_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** Fired when the server says the session has ended, so the app can show the sign-in page. */
export const SIGNED_OUT_EVENT = "patient-locker:signed-out";

function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => String((d as { msg?: string }).msg ?? "Invalid value").replace(/^Value error, /, ""))
        .join(" ");
    }
  }
  return `Request failed (${status}).`;
}

async function send<T>(path: string, method: string, body?: BodyInit, json = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  if (UNSAFE_METHODS.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const res = await fetch(`/api/v1${path}`, { method, headers, credentials: "same-origin", body });
  if (res.status === 204) return undefined as T;
  const parsed: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    }
    throw new ApiError(res.status, errorMessage(parsed, res.status));
  }
  return parsed as T;
}

export function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? "GET";
  const body = options.body === undefined ? undefined : JSON.stringify(options.body);
  return send<T>(path, method, body, options.body !== undefined);
}

/** Multipart upload: one file plus optional text fields. */
export function upload<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(fields)) if (value) form.append(key, value);
  return send<T>(path, "POST", form);
}
