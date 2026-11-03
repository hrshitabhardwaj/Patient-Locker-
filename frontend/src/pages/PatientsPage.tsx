import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  Button,
  Card,
  Empty,
  ErrorBox,
  inputClass,
  Labelled,
  Spinner,
  StatusBadge,
} from "../components/ui";
import { api } from "../lib/api";
import { LETTER_STATUS } from "../lib/labels";
import type { Page, Patient, PatientListItem } from "../lib/types";

const EMPTY_FORM = {
  full_name: "",
  phone: "",
  email: "",
  date_of_birth: "",
  policy_number: "",
  claim_number: "",
};

function NewPatientForm({ onCancel }: { onCancel: () => void }) {
  const [form, setForm] = useState(EMPTY_FORM);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () =>
      api<Patient>("/patients", {
        method: "POST",
        body: Object.fromEntries(Object.entries(form).filter(([, v]) => v.trim() !== "")),
      }),
    onSuccess: (patient) => {
      queryClient.invalidateQueries({ queryKey: ["patients"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-stats"] });
      navigate(`/patients/${patient.id}?created=1`);
    },
  });

  const field = (name: keyof typeof EMPTY_FORM) => ({
    value: form[name],
    onChange: (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value }),
    className: inputClass,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Card title="New patient">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <Labelled label="Full name *">
          <input required autoFocus {...field("full_name")} />
        </Labelled>
        <Labelled label="Mobile number *" hint="Used for WhatsApp. Add the country code if not Indian.">
          <input required type="tel" {...field("phone")} />
        </Labelled>
        <Labelled label="Email *">
          <input required type="email" {...field("email")} />
        </Labelled>
        <Labelled label="Date of birth">
          <input type="date" {...field("date_of_birth")} />
        </Labelled>
        <Labelled label="Policy number">
          <input {...field("policy_number")} />
        </Labelled>
        <Labelled label="Claim number" hint="Lets the system check a query letter belongs to this patient.">
          <input {...field("claim_number")} />
        </Labelled>
        <div className="sm:col-span-2">
          <ErrorBox error={create.error} />
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create patient"}
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

export default function PatientsPage() {
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const patients = useQuery({
    queryKey: ["patients", search],
    queryFn: () =>
      api<Page<PatientListItem>>(`/patients?limit=100${search ? `&q=${encodeURIComponent(search)}` : ""}`),
    refetchInterval: 10000,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Patients</h1>
          <p className="text-sm text-slate-500">
            {patients.data ? `${patients.data.total} patient(s) you can access` : " "}
          </p>
        </div>
        {!creating && <Button onClick={() => setCreating(true)}>+ New patient</Button>}
      </div>

      {creating && <NewPatientForm onCancel={() => setCreating(false)} />}

      <Card>
        <input
          type="search"
          placeholder="Search by name, Patient ID, phone, email, policy or claim number"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className={`${inputClass} mb-4`}
        />
        <ErrorBox error={patients.error} />
        {patients.isLoading ? (
          <Spinner label="Loading…" />
        ) : !patients.data?.items.length ? (
          <Empty>{search ? "No patients match your search." : "No patients yet. Create the first one."}</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500 uppercase">
                <tr>
                  <th className="py-2 pr-4">Patient ID</th>
                  <th className="py-2 pr-4">Name</th>
                  <th className="py-2 pr-4">Phone</th>
                  <th className="py-2 pr-4">Email</th>
                  <th className="py-2 pr-4 text-right">Documents</th>
                  <th className="py-2">Query status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {patients.data.items.map((patient) => (
                  <tr key={patient.id} className="hover:bg-slate-50">
                    <td className="py-2 pr-4 font-mono text-xs">{patient.patient_code}</td>
                    <td className="py-2 pr-4">
                      <Link to={`/patients/${patient.id}`} className="font-medium text-sky-800 hover:underline">
                        {patient.full_name}
                      </Link>
                    </td>
                    <td className="py-2 pr-4">{patient.phone}</td>
                    <td className="py-2 pr-4">{patient.email}</td>
                    <td className="py-2 pr-4 text-right">{patient.document_count}</td>
                    <td className="py-2">
                      {patient.latest_letter_status ? (
                        <StatusBadge map={LETTER_STATUS} code={patient.latest_letter_status} />
                      ) : (
                        <span className="text-slate-400">No query yet</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
