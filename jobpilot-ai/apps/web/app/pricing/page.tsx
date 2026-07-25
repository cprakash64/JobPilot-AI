import Link from "next/link";

export default function PricingPage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <Link href="/" className="text-sm text-pine">JobPilot AI</Link>
      <h1 className="mt-6 text-4xl font-semibold">Pricing</h1>
      <p className="mt-4 leading-7 text-[var(--text-muted)]">The open-source MVP is free to run locally. Hosted pricing is intentionally a placeholder until deployment costs, AI usage controls, and privacy commitments are finalized.</p>
    </main>
  );
}

