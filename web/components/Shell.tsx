"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, BarChart3, Home, Inbox, LogOut, Search, Settings, Sparkles, Workflow } from "lucide-react";
import { usePoll } from "@/lib/api";

const NAV = [
  { href: "/", icon: Home, label: "Command Center" },
  { href: "/approvals", icon: Inbox, label: "Approvals" },
  { href: "/theater", icon: Workflow, label: "Agent Theater" },
  { href: "/performance", icon: BarChart3, label: "Live Performance" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [approvals] = usePoll<unknown[]>("/approvals", 4000);
  const pending = approvals?.length ?? 0;

  return (
    <div className="app">
      <div className="mist" />
      <header className="relative z-10 flex items-center justify-between px-4 sm:px-8 pt-6">
        <Link href="/" className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-full" style={{ background: "var(--primary-ink)", color: "var(--canvas)" }}>
            <Sparkles size={18} />
          </span>
          <span className="text-[22px] tracking-tight" style={{ fontWeight: 500 }}>rune</span>
        </Link>
        <div className="flex items-center gap-2">
          <button className="circle" aria-label="Search"><Search size={16} /></button>
          <Link href="/approvals" className="circle relative" aria-label={`${pending} approvals waiting`}>
            <Bell size={16} />
            {pending > 0 && (
              <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold text-white" style={{ background: "var(--danger)" }}>
                {pending}
              </span>
            )}
          </Link>
          <span className="grid h-10 w-10 place-items-center rounded-full text-[13px] font-semibold" style={{ background: "var(--tile)", color: "var(--primary)" }}>SB</span>
        </div>
      </header>

      <div className="relative z-10 flex gap-6 px-4 sm:px-8 py-6">
        <nav className="hidden sm:flex w-10 shrink-0 flex-col justify-between" style={{ minHeight: "calc(100vh - 140px)" }}>
          <div className="flex flex-col gap-3 pt-12">
            {NAV.map(({ href, icon: Icon, label }) => (
              <Link key={href} href={href} title={label} aria-label={label} className={`circle ${path === href ? "active" : ""}`}>
                <Icon size={16} />
              </Link>
            ))}
          </div>
          <div className="flex flex-col gap-3">
            <button className="circle" aria-label="Settings"><Settings size={16} /></button>
            <button className="circle" aria-label="Log out"><LogOut size={16} /></button>
          </div>
        </nav>
        <main className="min-w-0 flex-1">{children}</main>
      </div>

      <nav className="sm:hidden fixed bottom-4 left-1/2 z-20 flex -translate-x-1/2 gap-3 card !p-2">
        {NAV.map(({ href, icon: Icon, label }) => (
          <Link key={href} href={href} aria-label={label} className={`circle ${path === href ? "active" : ""}`}>
            <Icon size={16} />
          </Link>
        ))}
      </nav>
    </div>
  );
}
