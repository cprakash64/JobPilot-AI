"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/Button";
import { api } from "@/lib/api";

const options = ["Prefer not to answer", "Yes", "No", "Another option"];

export function DemographicsForm() {
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({
    gender: "Prefer not to answer",
    veteran_status: "Prefer not to answer",
    disability_status: "Prefer not to answer",
    ethnicity: "Prefer not to answer",
    hispanic_latino_status: "Prefer not to answer",
    consent_to_store: false
  });

  async function save() {
    await api("/profile/demographics", { method: "PUT", body: JSON.stringify(form) });
    setMessage(form.consent_to_store ? "Sensitive demographics saved separately." : "Demographics removed because consent is off.");
  }

  async function remove() {
    await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/profile/demographics`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${localStorage.getItem("jobpilot_token")}` }
    });
    setMessage("Sensitive demographics deleted.");
  }

  return (
    <section className="rounded-lg border border-line bg-white p-5">
      <div className="rounded-md border border-line bg-panel p-4 text-sm leading-6 text-[#5d675f]">
        This information is optional. Prefer not to answer is always available. It is stored separately from your career profile, is not used for job-fit scoring, resume generation, or ranking, and can be deleted anytime.
      </div>
      <div className="mt-5 grid gap-4 md:grid-cols-2">
        {(["gender", "veteran_status", "disability_status", "ethnicity", "hispanic_latino_status"] as const).map((field) => (
          <label key={field}>
            <span className="text-sm font-medium">{field.replaceAll("_", " ")}</span>
            <select className="mt-2 h-10 w-full rounded-md border border-line px-3" value={form[field]} onChange={(event) => setForm((current) => ({ ...current, [field]: event.target.value }))}>
              {options.map((option) => <option key={option}>{option}</option>)}
            </select>
          </label>
        ))}
        <label className="flex items-center gap-3 rounded-md border border-line p-3">
          <input type="checkbox" checked={form.consent_to_store} onChange={(event) => setForm((current) => ({ ...current, consent_to_store: event.target.checked }))} />
          <span className="text-sm font-medium">I consent to store this optional information</span>
        </label>
      </div>
      <div className="mt-6 flex flex-wrap gap-3">
        <Button type="button" onClick={save}>Save settings</Button>
        <Button variant="danger" type="button" onClick={remove}><Trash2 className="h-4 w-4" /> Delete EEO data</Button>
        {message && <p className="self-center text-sm text-pine">{message}</p>}
      </div>
    </section>
  );
}

