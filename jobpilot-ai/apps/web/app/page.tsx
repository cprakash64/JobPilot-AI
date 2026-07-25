"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BriefcaseBusiness,
  Check,
  ChevronRight,
  FileCheck2,
  Search,
  ShieldCheck,
  UserRound
} from "lucide-react";
import { AuthDialog, type AuthMode } from "@/components/AuthDialog";

const workflow = [
  {
    number: "01",
    icon: UserRound,
    title: "Build your profile once",
    body: "Keep your experience, education, skills, and preferences in one truthful source of record."
  },
  {
    number: "02",
    icon: Search,
    title: "Focus on the right roles",
    body: "See fresh openings with clear fit signals, gaps, salary, location, and source details."
  },
  {
    number: "03",
    icon: FileCheck2,
    title: "Prepare, review, apply",
    body: "Tailor your resume and cover letter, autofill verified answers, then review before submitting."
  }
];

export default function HomePage() {
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);

  return (
    <main className="min-h-screen">
      <header className="border-b border-line/80 bg-white/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
          <Link href="/" className="focus-ring flex items-center gap-2.5 font-semibold tracking-[-0.02em]">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-pine text-white">
              <BriefcaseBusiness className="h-4 w-4" />
            </span>
            JobPilot
          </Link>
          <nav className="flex items-center gap-2">
            <button
              type="button"
              className="focus-ring hidden rounded-lg px-3 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-panel sm:inline-flex"
              onClick={() => setAuthMode("login")}
            >
              Sign in
            </button>
            <button
              type="button"
              className="focus-ring inline-flex h-10 items-center gap-1.5 rounded-lg bg-pine px-4 text-sm font-medium text-white transition hover:bg-[var(--accent-hover)]"
              onClick={() => setAuthMode("signup")}
            >
              Get started <ArrowRight className="h-4 w-4" />
            </button>
          </nav>
        </div>
      </header>

      <section className="border-b border-line/80 bg-white">
        <div className="mx-auto grid max-w-7xl gap-12 px-6 py-16 lg:grid-cols-[1.02fr_.98fr] lg:items-center lg:py-24">
          <div>
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3 py-1.5 text-xs font-semibold text-[var(--text-secondary)]">
              <span className="h-1.5 w-1.5 rounded-full bg-pine" />
              Your job search, organized
            </p>
            <h1 className="max-w-3xl text-5xl font-semibold leading-[1.04] tracking-[-0.045em] text-ink md:text-6xl">
              A calmer way to run your job search.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-[var(--text-muted)]">
              Find roles worth your time, prepare tailored application materials, and keep every application moving—all from one reliable profile.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <button
                type="button"
                className="focus-ring inline-flex h-12 items-center gap-2 rounded-xl bg-pine px-5 text-sm font-semibold text-white transition hover:bg-[var(--accent-hover)]"
                onClick={() => setAuthMode("signup")}
              >
                Create your profile <ArrowRight className="h-4 w-4" />
              </button>
              <a className="focus-ring inline-flex h-12 items-center gap-2 rounded-xl border border-line bg-white px-5 text-sm font-semibold text-[var(--text-secondary)] transition hover:bg-panel" href="#how-it-works">
                See how it works <ChevronRight className="h-4 w-4" />
              </a>
            </div>
            <p className="mt-5 flex items-center gap-2 text-sm text-[var(--text-muted)]">
              <ShieldCheck className="h-4 w-4 text-pine" />
              You review and submit every application.
            </p>
          </div>

          <div className="relative">
            <div className="rounded-[24px] border border-line bg-panel p-3 shadow-[0_20px_60px_rgb(23_33_27_/_10%)]">
              <div className="rounded-[18px] border border-line bg-white p-5 sm:p-6">
                <div className="flex items-start justify-between gap-4 border-b border-line pb-5">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.12em] text-pine">Next best match</p>
                    <h2 className="mt-2 text-xl font-semibold tracking-[-0.025em]">Senior Software Engineer</h2>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">Northstar · Remote · Posted today</p>
                  </div>
                  <div className="rounded-xl border border-[var(--success-border)] bg-[var(--success-surface)] px-3 py-2 text-center">
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">Fit</p>
                    <p className="text-2xl font-semibold leading-none text-pine">86</p>
                  </div>
                </div>

                <div className="py-5">
                  <p className="text-sm font-semibold">Application readiness</p>
                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-panel">
                    <div className="h-full w-3/4 rounded-full bg-pine" />
                  </div>
                  <div className="mt-4 grid gap-3">
                    <ReadinessRow label="Profile verified" meta="Complete" />
                    <ReadinessRow label="Resume tailored" meta="Ready" />
                    <ReadinessRow label="Cover letter" meta="Ready" />
                  </div>
                </div>

                <div className="flex items-center justify-between gap-4 rounded-xl bg-panel px-4 py-3">
                  <div>
                    <p className="text-sm font-semibold">Ready for your review</p>
                    <p className="mt-0.5 text-xs text-[var(--text-muted)]">Nothing is submitted without you.</p>
                  </div>
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-pine text-white">
                    <ArrowRight className="h-4 w-4" />
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="mx-auto max-w-7xl px-6 py-16 lg:py-20">
        <div className="max-w-2xl">
          <p className="text-sm font-semibold text-pine">One clear workflow</p>
          <h2 className="mt-2 text-3xl font-semibold tracking-[-0.035em] text-ink sm:text-4xl">
            From first search to final follow-up.
          </h2>
          <p className="mt-3 text-[var(--text-muted)]">
            Each step builds on the last, so you spend less time copying information and more time making good decisions.
          </p>
        </div>
        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {workflow.map((item) => (
            <article key={item.number} className="rounded-2xl border border-line bg-white p-6">
              <div className="flex items-center justify-between">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-[var(--success-surface)] text-pine">
                  <item.icon className="h-5 w-5" />
                </span>
                <span className="text-xs font-semibold tracking-[0.12em] text-[var(--text-muted)]">{item.number}</span>
              </div>
              <h3 className="mt-6 text-lg font-semibold tracking-[-0.02em]">{item.title}</h3>
              <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{item.body}</p>
            </article>
          ))}
        </div>
      </section>
      {authMode && (
        <AuthDialog
          key={authMode}
          initialMode={authMode}
          onClose={() => setAuthMode(null)}
        />
      )}
    </main>
  );
}

function ReadinessRow({ label, meta }: { label: string; meta: string }) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="grid h-6 w-6 place-items-center rounded-full bg-[var(--success-surface)] text-pine">
        <Check className="h-3.5 w-3.5" />
      </span>
      <span className="flex-1 text-[var(--text-secondary)]">{label}</span>
      <span className="text-xs font-medium text-[var(--text-muted)]">{meta}</span>
    </div>
  );
}
