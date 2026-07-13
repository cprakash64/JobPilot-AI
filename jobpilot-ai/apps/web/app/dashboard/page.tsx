import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { BriefcaseBusiness, ClipboardCheck, FileText, UserRound } from "lucide-react";

export default function DashboardPage() {
  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold">Dashboard</h1>
        <p className="mt-2 text-[#5d675f]">A focused workspace for profile facts, fresh jobs, generated materials, and manual applications.</p>
      </header>
      <div className="grid gap-4 md:grid-cols-4">
        <Tile href="/profile" icon={<UserRound />} title="Complete profile" body="Add truthful career facts once." />
        <Tile href="/jobs" icon={<BriefcaseBusiness />} title="Discover jobs" body="Find fresh jobs matched to your profile from official sources." />
        <Tile href="/jobs" icon={<FileText />} title="Generate materials" body="Create resume, letter, and answers per job." />
        <Tile href="/tracker" icon={<ClipboardCheck />} title="Track status" body="Move applications from saved to offer." />
      </div>
      <section className="mt-6 rounded-lg border border-line bg-white p-5">
        <h2 className="font-semibold">Compliance boundaries</h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-[#5d675f]">
          JobPilot AI assists with profile management, document generation, and approved source ingestion. It does not scrape restricted job boards, bypass bot detection, pretend to be human, or submit third-party applications automatically.
        </p>
      </section>
    </AppShell>
  );
}

function Tile({ href, icon, title, body }: { href: string; icon: React.ReactNode; title: string; body: string }) {
  return (
    <Link href={href} className="focus-ring rounded-lg border border-line bg-white p-5 hover:bg-panel">
      <div className="mb-4 h-8 w-8 text-pine">{icon}</div>
      <h2 className="font-semibold">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-[#5d675f]">{body}</p>
    </Link>
  );
}

