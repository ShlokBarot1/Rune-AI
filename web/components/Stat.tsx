import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

/** Stat card in the Mission Control style: one dark (ink) card per row, the rest glass. */
export default function Stat({ label, value, sub, dark = false, href, delta, invert = false }: {
  label: string; value: React.ReactNode; sub?: string; dark?: boolean; href?: string;
  delta?: number | null; invert?: boolean;
}) {
  const good = delta != null && (invert ? delta <= 0 : delta >= 0);
  return (
    <div className={`card !p-5 flex flex-col justify-between gap-3 ${dark ? "!text-white" : ""}`}
      style={dark ? { background: "var(--ink)", borderColor: "transparent" } : undefined}>
      <div className="flex items-center justify-between">
        <span className="label" style={dark ? { color: "#9aa3b2" } : undefined}>{label}</span>
        {href && (
          <Link href={href} className="circle sm" aria-label={`Open ${label}`}
            style={dark ? { background: "var(--accent)", color: "var(--accent-ink)", borderColor: "transparent" } : undefined}>
            <ArrowUpRight size={14} />
          </Link>
        )}
      </div>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="card-num">{value}</span>
        {delta != null && <span className={`pill ${good ? "ok" : "bad"}`}>{delta >= 0 ? "+" : ""}{(delta * 100).toFixed(1)}%</span>}
      </div>
      {sub && <div className="meta" style={dark ? { color: "#9aa3b2" } : undefined}>{sub}</div>}
    </div>
  );
}
