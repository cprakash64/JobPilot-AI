import Link from "next/link";

export default function OpenSourcePage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <Link href="/" className="text-sm text-pine">JobPilot AI</Link>
      <h1 className="mt-6 text-4xl font-semibold">Open source</h1>
      <p className="mt-4 leading-7 text-[var(--text-muted)]">The repository is structured as a Next.js frontend, FastAPI backend, shared schemas, Docker Compose infrastructure, CI, and contributor documentation under the MIT License.</p>
    </main>
  );
}

