import Link from "next/link";
import { BriefcaseBusiness, FileText, Gauge, Settings, ShieldCheck, UserRound } from "lucide-react";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: Gauge },
  { href: "/profile", label: "Profile", icon: UserRound },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { href: "/tracker", label: "Tracker", icon: FileText },
  { href: "/demographics", label: "EEO", icon: ShieldCheck },
  { href: "/settings", label: "Settings", icon: Settings }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[#fbfbf8]">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-line bg-white p-4 lg:block">
        <Link href="/" className="mb-8 flex items-center gap-2 font-semibold">
          <BriefcaseBusiness className="h-5 w-5 text-pine" />
          JobPilot AI
        </Link>
        <nav className="space-y-1">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="focus-ring flex items-center gap-3 rounded-md px-3 py-2 text-sm text-ink hover:bg-panel"
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="lg:pl-64">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </main>
    </div>
  );
}

