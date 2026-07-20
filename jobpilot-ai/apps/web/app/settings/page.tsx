import { AppShell } from "@/components/AppShell";
import { PrivacyControls } from "@/components/PrivacyControls";

export default function SettingsPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Settings and data controls</h1>
        <p className="mt-2 text-[var(--text-muted)]">Export all user data or delete the account and associated records.</p>
      </header>
      <PrivacyControls />
    </AppShell>
  );
}

