import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { CenteredCard, ErrorNote, Field, SubmitButton } from "../components/forms";

export default function ChangePasswordPage() {
  const { changePassword, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change the password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <CenteredCard title="Set a new password">
      <p className="mb-4 text-sm text-slate-600">
        You signed in with a temporary password. Choose your own before continuing.
      </p>
      <form onSubmit={onSubmit} className="space-y-4">
        <Field
          label="Temporary password"
          type="password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          hint="At least 12 characters, mixing letters with digits or symbols."
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        <Field
          label="Repeat new password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        {error && <ErrorNote>{error}</ErrorNote>}
        <SubmitButton busy={busy}>Save password</SubmitButton>
      </form>
      <button
        type="button"
        onClick={() => logout()}
        className="mt-4 w-full text-center text-sm text-slate-500 hover:text-slate-700"
      >
        Sign out
      </button>
    </CenteredCard>
  );
}
