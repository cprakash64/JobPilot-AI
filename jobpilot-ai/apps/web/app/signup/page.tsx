"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/Button";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("demo-password");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api<{ access_token: string }>("/auth/signup", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      localStorage.setItem("jobpilot_token", result.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signup failed.");
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
      <form onSubmit={submit} className="w-full rounded-lg border border-line bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-semibold">Create account</h1>
        <label className="mt-6 block text-sm font-medium">Email</label>
        <input className="mt-2 h-10 w-full rounded-md border border-line px-3" value={email} onChange={(event) => setEmail(event.target.value)} />
        <label className="mt-4 block text-sm font-medium">Password</label>
        <input className="mt-2 h-10 w-full rounded-md border border-line px-3" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        {error && <p className="mt-3 text-sm text-coral">{error}</p>}
        <Button className="mt-6 w-full" type="submit">Create account</Button>
      </form>
    </main>
  );
}
