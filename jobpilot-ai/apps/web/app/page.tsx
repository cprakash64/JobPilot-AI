import Link from "next/link";
import { ArrowRight, CheckCircle2, FileDown, ShieldCheck } from "lucide-react";

export default function HomePage() {
  return (
    <main>
      <section className="border-b border-line bg-white">
        <div className="mx-auto grid min-h-[82vh] max-w-7xl gap-8 px-6 py-12 lg:grid-cols-[1.05fr_.95fr] lg:items-center">
          <div>
            <p className="mb-4 text-sm font-semibold uppercase tracking-[0.18em] text-pine">
              Open-source application copilot
            </p>
            <h1 className="max-w-3xl text-5xl font-semibold leading-tight text-ink md:text-6xl">
              JobPilot AI
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-[var(--text-muted)]">
              Create one truthful career profile, discover fresh jobs from allowed sources, generate ATS-friendly materials, and track each application without stealth automation or mass submission.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link className="focus-ring inline-flex h-11 items-center gap-2 rounded-md bg-pine px-5 text-sm font-medium text-white" href="/signup">
                Start local MVP <ArrowRight className="h-4 w-4" />
              </Link>
              <Link className="focus-ring inline-flex h-11 items-center rounded-md border border-line px-5 text-sm font-medium" href="/opensource">
                View open-source scope
              </Link>
            </div>
          </div>
          <div className="rounded-lg border border-line bg-panel p-5 shadow-sm">
            <div className="grid gap-3">
              {[
                ["Profile facts", "Skills, education, projects, experience, links"],
                ["Fresh jobs", "Demo source plus Greenhouse and Lever adapters"],
                ["Guardrails", "Unsupported claims are flagged before export"],
                ["Manual apply", "Official links only; user reviews and submits"]
              ].map(([title, body]) => (
                <div key={title} className="rounded-md border border-line bg-white p-4">
                  <div className="flex items-start gap-3">
                    <CheckCircle2 className="mt-1 h-5 w-5 text-pine" />
                    <div>
                      <h2 className="font-medium">{title}</h2>
                      <p className="mt-1 text-sm text-[var(--text-muted)]">{body}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>
      <section className="mx-auto grid max-w-7xl gap-4 px-6 py-8 md:grid-cols-3">
        <Feature icon={<ShieldCheck />} title="Compliance first" body="No restricted scraping, fake typing, captcha bypass, or automatic third-party portal submission." />
        <Feature icon={<FileDown />} title="Editable outputs" body="Resume, cover letter, and application answers are saved per job and exported as DOCX or PDF." />
        <Feature icon={<ArrowRight />} title="User controlled" body="Sensitive demographic data is optional, separate, exportable, and deletable." />
      </section>
    </main>
  );
}

function Feature({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <article className="rounded-lg border border-line bg-white p-5">
      <div className="mb-4 h-9 w-9 text-pine">{icon}</div>
      <h2 className="font-semibold">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-[var(--text-muted)]">{body}</p>
    </article>
  );
}

