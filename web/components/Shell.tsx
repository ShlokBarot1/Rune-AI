"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, Bell, Home, Images, Inbox, Orbit, Plus, Search, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import { usePoll } from "@/lib/api";

const CREATE = [
  { href: "/run", icon: Orbit, label: "Mission Control" },
  { href: "/onboarding", icon: Plus, label: "New campaign" },
  { href: "/studio", icon: Images, label: "Creative Studio" },
  { href: "/audit", icon: ShieldCheck, label: "Audit Report" },
];
const OPERATE = [
  { href: "/", icon: Home, label: "Command Center" },
  { href: "/approvals", icon: Inbox, label: "Approvals" },
  { href: "/theater", icon: Workflow, label: "Agent Theater" },
  { href: "/performance", icon: BarChart3, label: "Live Performance" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [approvals] = usePoll<unknown[]>("/approvals", 4000);
  const pending = approvals?.length ?? 0;
  const item = ({ href, icon: Icon, label }: (typeof CREATE)[number]) => (
    <Link key={href} href={href} className={`nav-item ${path === href ? "active" : ""}`}>
      <Icon size={16} /> {label}
      {href === "/approvals" && pending > 0 && <span className="count">{pending}</span>}
    </Link>
  );

  return (
    <div className="app">
      <div className="mist" />
      <div className="relative z-10 flex gap-6 p-4 sm:p-6">
        <aside className="sidebar hidden lg:flex w-[232px] shrink-0 flex-col gap-6 p-4 sticky top-6 self-start" style={{ height: "calc(100vh - 48px)" }}>
          <Link href="/run" className="flex items-center gap-2.5 px-2 pt-1">
            <span className="grid h-9 w-9 place-items-center rounded-full" style={{ background: "var(--ink)", color: "var(--accent)" }}>
              <Sparkles size={16} />
            </span>
            <span className="text-[20px] font-semibold tracking-tight">rune</span>
            <span className="pill lime ml-auto">AI</span>
          </Link>
          <nav className="flex flex-col gap-1">
            <div className="meta px-3 pb-1 uppercase tracking-wider">Create</div>
            {CREATE.map(item)}
          </nav>
          <nav className="flex flex-col gap-1">
            <div className="meta px-3 pb-1 uppercase tracking-wider">Launch &amp; optimise</div>
            {OPERATE.map(item)}
          </nav>
          <Link href="/onboarding" className="mt-auto rounded-[22px] p-4 text-white" style={{ background: "var(--ink)" }}>
            <span className="grid h-9 w-9 place-items-center rounded-full" style={{ background: "var(--accent)", color: "var(--accent-ink)" }}>
              <Plus size={16} />
            </span>
            <div className="mt-3 text-[15px] font-semibold leading-tight">Launch a new brand</div>
            <div className="mt-1 text-[12px] opacity-60">Paste a URL, the agents do the rest.</div>
          </Link>
        </aside>

        <div className="min-w-0 flex-1">
          <header className="flex items-center justify-end gap-2 pb-4">
            <Link href="/run" className="lg:hidden mr-auto flex items-center gap-2 font-semibold"><Sparkles size={16} /> rune</Link>
            <button className="circle" aria-label="Search"><Search size={16} /></button>
            <Link href="/approvals" className="circle relative" aria-label={`${pending} approvals waiting`}>
              <Bell size={16} />
              {pending > 0 && (
                <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-bold" style={{ background: "var(--accent)", color: "var(--accent-ink)" }}>
                  {pending}
                </span>
              )}
            </Link>
            <div className="flex items-center gap-2 rounded-full bg-white/80 py-1 pl-1 pr-4 border" style={{ borderColor: "var(--line)" }}>
              <span className="grid h-8 w-8 place-items-center rounded-full text-[12px] font-semibold text-white" style={{ background: "var(--primary)" }}>RA</span>
              <div className="leading-tight">
                <div className="meta">Agency</div>
                <div className="text-[12.5px] font-semibold">Rune Studio</div>
              </div>
            </div>
          </header>
          <main>{children}</main>
        </div>
      </div>

      <nav className="lg:hidden fixed bottom-4 left-1/2 z-20 flex -translate-x-1/2 gap-2 card !p-2 !rounded-full">
        {[...CREATE, ...OPERATE.slice(0, 2)].map(({ href, icon: Icon, label }) => (
          <Link key={href} href={href} aria-label={label} className={`circle sm ${path === href ? "active" : ""}`}>
            <Icon size={15} />
          </Link>
        ))}
      </nav>
    </div>
  );
}
