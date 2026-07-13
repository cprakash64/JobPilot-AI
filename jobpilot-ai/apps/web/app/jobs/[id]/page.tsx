import Link from "next/link";
import { AppShell } from "@/components/AppShell";

export default async function JobDetailsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Job details</h1>
        <p className="mt-2 text-[#5d675f]">Job ID {id}. Use the discovery page to generate documents and open the official application link.</p>
      </header>
      <div className="rounded-lg border border-line bg-white p-5">
        <Link className="text-pine" href="/jobs">Back to job discovery</Link>
      </div>
    </AppShell>
  );
}
