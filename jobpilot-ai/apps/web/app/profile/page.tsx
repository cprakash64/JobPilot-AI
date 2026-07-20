import { AppShell } from "@/components/AppShell";
import { ProfileWizard } from "@/components/ProfileWizard";

export default function ProfilePage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Profile manager</h1>
        <p className="mt-2 text-[var(--text-muted)]">Import, review, and maintain truthful career facts before generating job-specific materials.</p>
      </header>
      <ProfileWizard />
    </AppShell>
  );
}
