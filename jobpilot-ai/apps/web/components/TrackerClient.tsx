"use client";

import { useState } from "react";
import { Button } from "@/components/Button";
import { api } from "@/lib/api";

type Application = {
  id: number;
  job_id: number;
  status: string;
  notes?: string | null;
  applied_at?: string | null;
};

export function TrackerClient() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [message, setMessage] = useState("");

  async function load() {
    const result = await api<{ applications: Application[] }>("/jobs/tracker/all");
    setApplications(result.applications);
  }

  async function update(jobId: number, status: string) {
    await api(`/jobs/${jobId}/tracker`, {
      method: "PUT",
      body: JSON.stringify({ status })
    });
    setMessage("Status updated.");
    await load();
  }

  return (
    <section className="rounded-lg border border-line bg-white p-5">
      <div className="mb-4 flex gap-3">
        <Button type="button" onClick={load}>Load applications</Button>
        {message && <p className="self-center text-sm text-pine">{message}</p>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-sm">
          <thead className="border-b border-line text-[var(--text-muted)]">
            <tr>
              <th className="py-3">Job ID</th>
              <th>Status</th>
              <th>Applied</th>
              <th>Move</th>
            </tr>
          </thead>
          <tbody>
            {applications.map((row) => (
              <tr key={row.id} className="border-b border-line">
                <td className="py-3">{row.job_id}</td>
                <td>{row.status}</td>
                <td>{row.applied_at ?? "Not marked"}</td>
                <td className="flex flex-wrap gap-2 py-2">
                  {["ready_to_apply", "applied", "interview", "rejected", "offer"].map((status) => (
                    <button key={status} className="focus-ring rounded-md border border-line px-2 py-1 text-xs" onClick={() => update(row.job_id, status)} type="button">
                      {status.replaceAll("_", " ")}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {applications.length === 0 && <p className="mt-4 text-sm text-[var(--text-muted)]">Saved jobs will appear here.</p>}
    </section>
  );
}

