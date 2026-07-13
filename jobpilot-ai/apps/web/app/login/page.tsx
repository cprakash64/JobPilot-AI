"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "@/components/Button";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("demo-password");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      localStorage.setItem("jobpilot_token", result.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    }
  }

  return <AuthForm title="Log in" email={email} setEmail={setEmail} password={password} setPassword={setPassword} error={error} submit={submit} cta="Log in" />;
}

function AuthForm(props: {
  title: string;
  email: string;
  setEmail: (value: string) => void;
  password: string;
  setPassword: (value: string) => void;
  error: string;
  submit: (event: React.FormEvent) => void;
  cta: string;
}) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md items-center px-6">
      <form onSubmit={props.submit} className="w-full rounded-lg border border-line bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-semibold">{props.title}</h1>
        <label className="mt-6 block text-sm font-medium">Email</label>
        <input className="mt-2 h-10 w-full rounded-md border border-line px-3" value={props.email} onChange={(event) => props.setEmail(event.target.value)} />
        <label className="mt-4 block text-sm font-medium">Password</label>
        <input className="mt-2 h-10 w-full rounded-md border border-line px-3" type="password" value={props.password} onChange={(event) => props.setPassword(event.target.value)} />
        {props.error && <p className="mt-3 text-sm text-coral">{props.error}</p>}
        <Button className="mt-6 w-full" type="submit">{props.cta}</Button>
      </form>
    </main>
  );
}
