"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BriefcaseBusiness,
  FileText,
  Gauge,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  UserRound
} from "lucide-react";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: Gauge },
  { href: "/profile", label: "Profile", icon: UserRound },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { href: "/tracker", label: "Tracker", icon: FileText }
];

const settingsItem = { href: "/settings", label: "Settings", icon: Settings };

type AppShellApi = {
  collapsed: boolean;
  /**
   * A page asks for the rail (e.g. the Jobs detail workspace opened). The user's
   * own expand/collapse choice wins until the request itself changes, so
   * re-expanding the sidebar while reading a job is not undone on every render.
   */
  requestCollapsed: (collapsed: boolean) => void;
};

const AppShellContext = createContext<AppShellApi | null>(null);

/** Null outside an AppShell, so components remain renderable on their own. */
export function useAppShell(): AppShellApi | null {
  return useContext(AppShellContext);
}

export function AppShell({
  children,
  /** Full-bleed pages (the Jobs master-detail workspace) manage their own
   * padding and scroll containers instead of the centred page container. */
  workspace = false
}: {
  children: React.ReactNode;
  workspace?: boolean;
}) {
  // One state object keeps the two inputs consistent: a new request from the
  // page always clears a stale user override, without a side effect inside an
  // updater.
  const [state, setState] = useState<{ requested: boolean; override: boolean | null }>({
    requested: false,
    override: null
  });
  const collapsed = state.override ?? state.requested;

  const requestCollapsed = useCallback((value: boolean) => {
    setState((current) =>
      current.requested === value ? current : { requested: value, override: null }
    );
  }, []);

  const toggle = useCallback(() => {
    setState((current) => ({ ...current, override: !(current.override ?? current.requested) }));
  }, []);

  const api = useMemo<AppShellApi>(() => ({ collapsed, requestCollapsed }), [collapsed, requestCollapsed]);

  return (
    <AppShellContext.Provider value={api}>
      {/* A workspace page sizes itself to the *dynamic* viewport, matching the
        * detail panel's own 100dvh. min-h-screen (100vh) would be taller than
        * the visible area wherever browser chrome collapses, leaving the whole
        * page scrollable behind a full-height layout. */}
      <div className={`bg-[var(--background)] ${workspace ? "min-h-[100dvh]" : "min-h-screen"}`}>
        <SideBar collapsed={collapsed} onToggle={toggle} />
        <main className={collapsed ? "lg:pl-14" : "lg:pl-64"}>
          {workspace ? children : <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>}
        </main>
      </div>
    </AppShellContext.Provider>
  );
}

function SideBar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const pathname = usePathname();
  const isActive = (href: string) => pathname === href || pathname?.startsWith(`${href}/`);

  return (
    <aside
      aria-label="Primary"
      data-collapsed={collapsed ? "true" : "false"}
      className={`fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-line bg-white transition-[width] duration-150 lg:flex ${
        collapsed ? "w-14 px-2 py-3" : "w-64 p-4"
      }`}
    >
      <Link
        href="/"
        aria-label="JobPilot AI home"
        className={`focus-ring flex items-center rounded-lg font-semibold ${
          collapsed ? "justify-center py-2" : "mb-6 gap-2 px-1 py-1"
        }`}
      >
        <span
          aria-hidden
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--success-surface)] text-pine"
        >
          <BriefcaseBusiness className="h-4 w-4" />
        </span>
        {collapsed ? <span className="sr-only">JobPilot AI</span> : <span>JobPilot AI</span>}
      </Link>

      <nav aria-label="Sections" className={collapsed ? "mt-3 space-y-1" : "space-y-1"}>
        {nav.map((item) => (
          <NavItem key={item.href} {...item} collapsed={collapsed} active={Boolean(isActive(item.href))} />
        ))}
      </nav>

      <div className={`mt-auto space-y-1 ${collapsed ? "" : "pt-4"}`}>
        <NavItem
          {...settingsItem}
          collapsed={collapsed}
          active={Boolean(isActive(settingsItem.href))}
        />
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`focus-ring group relative flex w-full items-center rounded-md py-2 text-sm text-[var(--text-muted)] hover:bg-panel ${
            collapsed ? "justify-center px-0" : "gap-3 px-3"
          }`}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" aria-hidden />
          ) : (
            <PanelLeftClose className="h-4 w-4" aria-hidden />
          )}
          {collapsed ? <RailTooltip>Expand sidebar</RailTooltip> : <span>Collapse sidebar</span>}
        </button>
      </div>
    </aside>
  );
}

function NavItem({
  href,
  label,
  icon: Icon,
  collapsed,
  active
}: {
  href: string;
  label: string;
  icon: typeof Gauge;
  collapsed: boolean;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      aria-label={collapsed ? label : undefined}
      aria-current={active ? "page" : undefined}
      className={`focus-ring group relative flex items-center rounded-md py-2 text-sm transition-colors ${
        collapsed ? "justify-center px-0" : "gap-3 px-3"
      } ${
        active
          ? "bg-[var(--success-surface)] font-medium text-pine"
          : "text-ink hover:bg-panel"
      }`}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden />
      {collapsed ? <RailTooltip>{label}</RailTooltip> : <span>{label}</span>}
    </Link>
  );
}

/**
 * Tooltip for the icon-only rail. The control's accessible name comes from
 * aria-label, so this is presentational: it must never be the only way to learn
 * what a control does.
 */
function RailTooltip({ children }: { children: React.ReactNode }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-full z-40 ml-2 hidden whitespace-nowrap rounded-md border border-line bg-white px-2 py-1 text-xs text-ink shadow-card group-hover:block group-focus-visible:block"
    >
      {children}
    </span>
  );
}
