import { AppShell } from "@/components/AppShell";
import { JobDiscovery } from "@/components/JobDiscovery";

export default function JobsPage() {
  // The Jobs workspace manages its own page container: in list mode it centres
  // a readable column, and with a job open it fills the viewport with the
  // compact list and the detail panel.
  return (
    <AppShell workspace>
      <JobDiscovery />
    </AppShell>
  );
}
