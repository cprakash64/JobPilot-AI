"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, BriefcaseBusiness, Eye, EyeOff, Loader2, LockKeyhole, X } from "lucide-react";
import { api } from "@/lib/api";

export type AuthMode = "login" | "signup";

export function AuthDialog({
  initialMode,
  presentation = "modal",
  onClose
}: {
  initialMode: AuthMode;
  presentation?: "modal" | "page";
  onClose?: () => void;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("demo-password");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (presentation !== "modal") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose?.();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, presentation, submitting]);

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setError("");
    setSubmitting(true);
    try {
      const result = await api<{ access_token: string }>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      localStorage.setItem("jobpilot_token", result.access_token);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : mode === "login" ? "Login failed." : "Signup failed.");
      setSubmitting(false);
    }
  }

  const isLogin = mode === "login";
  const card = (
    <section
      className="auth-dialog w-full max-w-[480px] overflow-hidden rounded-[28px]"
      role={presentation === "modal" ? "dialog" : undefined}
      aria-modal={presentation === "modal" ? "true" : undefined}
      aria-labelledby="auth-dialog-title"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="px-7 pb-5 pt-7 sm:px-9 sm:pt-9">
        <div className="flex items-start justify-between gap-5">
          <span className="grid h-10 w-10 place-items-center rounded-xl border border-[var(--glass-border)] bg-[var(--success-surface)] text-pine">
            <BriefcaseBusiness className="h-5 w-5" />
          </span>
          {presentation === "modal" && (
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="focus-ring grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line bg-white/50 text-[var(--text-secondary)] transition hover:bg-panel"
              aria-label="Close authentication"
            >
              <X className="h-[18px] w-[18px]" />
            </button>
          )}
        </div>
        <h1 id="auth-dialog-title" className="mt-7 text-3xl font-semibold tracking-[-0.04em]">
          {isLogin ? "Sign in to JobPilot" : "Create your account"}
        </h1>
        <p className="mt-2 text-sm text-[var(--text-muted)]">
          {isLogin ? "Continue your job search." : "One profile for every application."}
        </p>
      </div>

      <form onSubmit={submit} className="px-7 pb-7 sm:px-9 sm:pb-9">
        <label className="block text-sm font-medium" htmlFor="auth-email">Email address</label>
        <input
          id="auth-email"
          className="auth-input mt-2 h-12 w-full rounded-xl border border-line px-4 text-sm"
          type="email"
          inputMode="email"
          autoComplete="email"
          autoFocus={presentation === "modal"}
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <div className="mt-5 flex items-center justify-between">
          <label className="text-sm font-medium" htmlFor="auth-password">Password</label>
        </div>
        <div className="relative mt-2">
          <input
            id="auth-password"
            className="auth-input h-12 w-full rounded-xl border border-line px-4 pr-12 text-sm"
            type={showPassword ? "text" : "password"}
            autoComplete={isLogin ? "current-password" : "new-password"}
            minLength={8}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button
            className="focus-ring absolute right-1.5 top-1.5 grid h-9 w-9 place-items-center rounded-lg text-[var(--text-muted)] hover:bg-panel"
            type="button"
            onClick={() => setShowPassword((visible) => !visible)}
            aria-label={showPassword ? "Hide password" : "Show password"}
          >
            {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>

        {error && (
          <p className="mt-4 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-surface)] px-3 py-2.5 text-sm text-[var(--danger)]" role="alert">
            {error}
          </p>
        )}

        <button
          className="focus-ring mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-pine px-5 text-sm font-semibold text-white transition hover:bg-[var(--accent-hover)] disabled:opacity-70"
          type="submit"
          disabled={submitting}
        >
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
          {submitting ? (isLogin ? "Logging in…" : "Creating account…") : (isLogin ? "Log in" : "Create account")}
        </button>

        <div className="mt-5 flex items-center justify-center gap-1.5 text-sm text-[var(--text-muted)]">
          <span>{isLogin ? "New to JobPilot?" : "Already have an account?"}</span>
          <button
            type="button"
            className="focus-ring rounded-md px-1 py-0.5 font-semibold text-pine"
            onClick={() => changeMode(isLogin ? "signup" : "login")}
          >
            {isLogin ? "Create an account" : "Sign in"}
          </button>
        </div>

        <p className="mt-6 flex items-center justify-center gap-2 border-t border-line/70 pt-5 text-center text-xs text-[var(--text-muted)]">
          <LockKeyhole className="h-3.5 w-3.5 shrink-0 text-pine" />
          Private by default. You stay in control.
        </p>
      </form>
    </section>
  );

  if (presentation === "page") {
    return (
      <main className="grid min-h-screen place-items-center px-4 py-10">
        {card}
      </main>
    );
  }

  return (
    <div className="auth-backdrop fixed inset-0 z-50 grid place-items-center overflow-y-auto p-4 sm:p-6" onClick={submitting ? undefined : onClose}>
      {card}
    </div>
  );
}
