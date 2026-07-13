import { AppShell } from "@/components/AppShell";
import { JobDiscovery } from "@/components/JobDiscovery";

export default function JobsPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Job discovery</h1>
        <p className="mt-2 text-[#5d675f]">Fresh jobs matched to your profile from official application sources.</p>
      </header>
      <JobDiscovery />
    </AppShell>
  );
}

