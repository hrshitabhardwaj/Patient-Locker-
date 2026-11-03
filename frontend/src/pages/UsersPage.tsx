import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { Badge, Button, Card, ErrorBox, inputClass, Labelled, Notice, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/labels";
import type { Page } from "../lib/types";

interface StaffUser {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "case_manager" | "staff";
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
}

const ROLES = [
  ["staff", "Staff — assigned patients only"],
  ["case_manager", "Case manager — all patients, assigns staff"],
  ["admin", "Administrator — everything, incl. staff and audit log"],
] as const;

function TemporaryPassword({ who, password, onDone }: { who: string; password: string; onDone: () => void }) {
  return (
    <Notice tone="green">
      <p>
        Temporary password for <strong>{who}</strong>:{" "}
        <code className="rounded bg-white px-2 py-0.5 font-mono text-base">{password}</code>
      </p>
      <p className="mt-1 text-xs">
        Shown only once. Give it to them securely; they must choose their own password at first sign-in.
      </p>
      <Button variant="ghost" className="mt-1" onClick={onDone}>
        Done
      </Button>
    </Notice>
  );
}

export default function UsersPage() {
  const { state } = useAuth();
  const me = state.status === "signed_in" ? state.user : null;
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ email: "", full_name: "", role: "staff" });
  const [issued, setIssued] = useState<{ who: string; password: string } | null>(null);
  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => api<Page<StaffUser>>("/users?limit=200"),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const create = useMutation({
    mutationFn: () =>
      api<{ user: StaffUser; temporary_password: string }>("/users", { method: "POST", body: form }),
    onSuccess: (res) => {
      setIssued({ who: res.user.email, password: res.temporary_password });
      setForm({ email: "", full_name: "", role: "staff" });
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api(`/users/${id}`, { method: "PATCH", body }),
    onSuccess: refresh,
  });
  const reset = useMutation({
    mutationFn: (user: StaffUser) =>
      api<{ temporary_password: string }>(`/users/${user.id}/reset-password`, { method: "POST" }).then(
        (res) => ({ who: user.email, password: res.temporary_password }),
      ),
    onSuccess: (result) => {
      setIssued(result);
      refresh();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Staff accounts</h1>
        <p className="text-sm text-slate-500">
          There is no public sign-up. New accounts get a one-time temporary password.
        </p>
      </div>

      {issued && <TemporaryPassword {...issued} onDone={() => setIssued(null)} />}

      <Card title="Add a staff member">
        <form onSubmit={submit} className="grid gap-4 sm:grid-cols-3">
          <Labelled label="Full name">
            <input required className={inputClass} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </Labelled>
          <Labelled label="Work email">
            <input required type="email" className={inputClass} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Labelled>
          <Labelled label="Role">
            <select className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Labelled>
          <div className="sm:col-span-3">
            <ErrorBox error={create.error} />
            <Button type="submit" disabled={create.isPending} className="mt-2">
              {create.isPending ? "Creating…" : "Create account"}
            </Button>
          </div>
        </form>
      </Card>

      <Card title="Accounts">
        <ErrorBox error={users.error ?? update.error ?? reset.error} />
        {users.isLoading ? (
          <Spinner label="Loading…" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500 uppercase">
                <tr>
                  <th className="py-2 pr-4">Name</th>
                  <th className="py-2 pr-4">Role</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Last sign-in</th>
                  <th className="py-2">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.data?.items.map((user) => {
                  const self = user.id === me?.id;
                  return (
                    <tr key={user.id} className={user.is_active ? "" : "text-slate-400"}>
                      <td className="py-2 pr-4">
                        <span className="font-medium">{user.full_name}</span>
                        {self && <span className="ml-1 text-xs text-slate-500">(you)</span>}
                        <span className="block text-xs text-slate-500">{user.email}</span>
                      </td>
                      <td className="py-2 pr-4">
                        <select
                          disabled={self || !user.is_active}
                          className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
                          value={user.role}
                          onChange={(e) => update.mutate({ id: user.id, body: { role: e.target.value } })}
                        >
                          {ROLES.map(([value]) => (
                            <option key={value} value={value}>
                              {value.replace("_", " ")}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-2 pr-4">
                        {user.is_active ? <Badge tone="green">Active</Badge> : <Badge>Deactivated</Badge>}
                        {user.must_change_password && user.is_active && (
                          <span className="block text-xs text-slate-500">temporary password</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-slate-500">{formatDateTime(user.last_login_at)}</td>
                      <td className="py-2">
                        {!self && (
                          <div className="flex flex-wrap gap-1">
                            <Button variant="secondary" onClick={() => update.mutate({ id: user.id, body: { is_active: !user.is_active } })}>
                              {user.is_active ? "Deactivate" : "Reactivate"}
                            </Button>
                            {user.is_active && (
                              <Button variant="ghost" onClick={() => reset.mutate(user)}>
                                Reset password
                              </Button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
