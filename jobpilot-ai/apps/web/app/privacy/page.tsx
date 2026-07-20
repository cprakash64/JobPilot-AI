import Link from "next/link";

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <Link href="/" className="text-sm text-pine">JobPilot AI</Link>
      <h1 className="mt-6 text-4xl font-semibold">Privacy</h1>
      <p className="mt-4 leading-7 text-[var(--text-muted)]">Users control profile data, generated documents, optional EEO information, exports, and deletion. Sensitive demographics are stored separately and excluded from matching and resume generation.</p>
    </main>
  );
}

