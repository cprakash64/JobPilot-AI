import { AppShell } from "@/components/AppShell";
import { TrackerClient } from "@/components/TrackerClient";

export default function TrackerPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Application tracker</h1>
        <p className="mt-2 text-[var(--text-muted)]">
          Every application in one place, from first click to interview and offer.
        </p>
      </header>
      <TrackerClient />
    </AppShell>
  );
}
