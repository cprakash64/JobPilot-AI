import { AppShell } from "@/components/AppShell";
import { DemographicsForm } from "@/components/DemographicsForm";

export default function DemographicsPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Optional EEO settings</h1>
        <p className="mt-2 text-[#5d675f]">Manage voluntary demographic data separately from your career profile.</p>
      </header>
      <DemographicsForm />
    </AppShell>
  );
}

