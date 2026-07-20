import { AppShell } from "@/components/AppShell";

export default function ApplicationAnswersPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Application answers</h1>
        <p className="mt-2 text-[var(--text-muted)]">Prepare editable answers for common application questions. EEO answers are only included when the user explicitly chooses to use stored voluntary data.</p>
      </header>
    </AppShell>
  );
}

