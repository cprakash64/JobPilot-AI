"use client";

import { useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/Button";
import { api } from "@/lib/api";

export function PrivacyControls() {
  const [exported, setExported] = useState("");
  const [message, setMessage] = useState("");

  async function exportData() {
    const result = await api<Record<string, unknown>>("/privacy/export");
    setExported(JSON.stringify(result, null, 2));
  }

  async function deleteAccount() {
    await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/privacy/account`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${localStorage.getItem("jobpilot_token")}` }
    });
    localStorage.removeItem("jobpilot_token");
    setMessage("Account deleted.");
  }

  return (
    <section className="rounded-lg border border-line bg-white p-5">
      <div className="flex flex-wrap gap-3">
        <Button type="button" onClick={exportData}><Download className="h-4 w-4" /> Export JSON</Button>
        <Button variant="danger" type="button" onClick={deleteAccount}><Trash2 className="h-4 w-4" /> Delete account</Button>
        {message && <p className="self-center text-sm text-coral">{message}</p>}
      </div>
      {exported && <pre className="mt-5 max-h-[520px] overflow-auto rounded-md bg-[var(--text-primary)] p-4 text-xs text-white">{exported}</pre>}
    </section>
  );
}

