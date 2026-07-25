import { AppShell } from "@/components/AppShell";

export default function ResumePage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Resume generation</h1>
        <p className="mt-2 text-[var(--text-muted)]">Generate tailored, ATS-friendly resumes from the job discovery page. The backend stores every version per job and checks unsupported claims.</p>
      </header>
    </AppShell>
  );
}

