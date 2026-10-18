"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, Check, FastForward, Inbox, Plus, Workflow } from "lucide-react";
import Avatar from "@/components/Avatar";
import Bars from "@/components/Bars";
import { ClientPicker, useClient } from "@/components/useClient";
import { GATE_TITLES, ago, fileUrl, money, msgText, pct, post, useEvents, usePoll, type Json } from "@/lib/api";

export default function CommandCenter() {
  const { runs, err, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 3000);
  const live = !!run?.values?.live;
  const [perf, , reloadPerf] = usePoll<Json>(clientId && live ? `/runs/${clientId}/performance` : null, 5000);
  const [approvals] = usePoll<Json[]>("/approvals", 3000);
  const events = useEvents(null, 60);
  const [busy, setBusy] = useState("");

  const v = run?.values ?? {};
  const cur = v.brief?.budget?.currency ?? "INR";
  const total = v.budget_plan?.total ?? v.brief?.budget?.total ?? 0;
  const spent = perf?.totals?.spend ?? 0;
  const t = perf?.totals ?? {};
  const daily: Json[] = perf?.daily ?? [];
  const lastWeek = daily.slice(-7);
  const prevWeek = daily.slice(-14, -7);
  const sum = (xs: Json[], k: string) => xs.reduce((a, d) => a + (d[k] ?? 0), 0);
  const delta = (k: string) => {
    const a = sum(lastWeek, k), b = sum(prevWeek, k);
    return b ? (a - b) / b : null;
  };
  const nextAction = perf?.actions?.find((a: Json) => a.risk === "approval" && a.status === "proposed");
  const best = Object.entries<Json>(perf?.stats ?? {}).sort((a, b) => (a[1].cpa ?? 1e9) - (b[1].cpa ?? 1e9))[0];
  const bestCreative = best && v.package?.creatives?.find((c: Json) => c.creative_id === best[0]);
  const feed = [...events].reverse().filter((m) => m.type !== "RESPONSE" || m.sender !== "human").slice(0, 7);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try { await fn(); } finally { setBusy(""); reloadPerf(); }
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] tracking-tight" style={{ fontWeight: 500 }}>Command Center</h1>
          <p className="label mt-1">{live ? "Ads are live — agents monitor every few hours." : run?.next?.length ? `Pipeline at: ${run.next.join(", ")}` : "Start a client to begin."}</p>
        </div>
        <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
      </div>
      {err && <div className="card label">API unreachable ({err}). Run <code>uvicorn api:app --port 8000</code> in backend/.</div>}

      <div className="grid gap-5 lg:grid-cols-[1.15fr_1fr_0.95fr]">
        {/* ---------- left column ---------- */}
        <div className="flex min-w-0 flex-col gap-5">
          <section className="card">
            <div className="flex items-center justify-between">
              <span className="label">Budget remaining</span>
              <Link href="/performance" className="circle !h-8 !w-8 !bg-[var(--primary)] !text-white" aria-label="Performance"><ArrowUpRight size={14} /></Link>
            </div>
            <div className="hero-num mt-3">{money(total - spent, cur)}</div>
            <div className="mt-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px]" style={{ background: "var(--tile)", color: "var(--text-2)" }}>
              Spent {money(spent, cur)} of {money(total, cur)}
              {v.budget_plan?.testing_reserve ? <> · reserve <b className="font-semibold">{money(v.budget_plan.testing_reserve, cur)}</b>/day</> : null}
            </div>
            <div className="label mt-6 mb-3">Quick actions</div>
            <div className="grid grid-cols-4 gap-2 text-center">
              <QA icon={<Plus size={16} />} label="New client" busy={busy === "new"} onClick={() => act("new", () => post("/runs", {}).then((r: Json) => setClientId(r.client_id)))} />
              <QA icon={<Inbox size={16} />} label="Approvals" href="/approvals" />
              <QA icon={<Workflow size={16} />} label="Theater" href="/theater" />
              <QA icon={<FastForward size={16} />} label="Next week" disabled={!live} busy={busy === "cycle"}
                onClick={() => act("cycle", () => post(`/runs/${clientId}/cycle`, { day: daily.length, days: 7 }))} />
            </div>
          </section>

          <section className="card">
            <div className="flex items-center justify-between"><span className="title">Agent activity</span><Link href="/theater" className="meta hover:underline">View all</Link></div>
            <div className="mt-2">
              {feed.length === 0 && <div className="meta py-4">Waiting for agents…</div>}
              {feed.map((m) => (
                <div key={m.seq} className="row fade-in">
                  <Avatar name={m.sender} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium">{m.sender}</div>
                    <div className="truncate meta">{msgText(m)}</div>
                  </div>
                  <span className="meta shrink-0">{ago(m.created_at)}</span>
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* ---------- middle column ---------- */}
        <div className="flex min-w-0 flex-col gap-5">
          <div className="grid grid-cols-2 gap-5">
            <Stat label="Revenue" value={money(t.revenue, cur)} d={delta("revenue")} sub={`ROAS ${(t.roas ?? 0).toFixed(2)}`} />
            <Stat label="Ad spend" value={money(t.spend, cur)} d={delta("spend")} invert sub={`CPA ${money(t.cpa, cur)}`} />
          </div>
          <section className="card">
            <div className="flex items-center justify-between"><span className="label">Conversions</span><span className="meta">Last 7 days</span></div>
            <div className="mt-2 flex items-end justify-between gap-4">
              <div>
                <div className="hero-num !text-[34px]">{sum(lastWeek, "conversions")}</div>
                <div className="meta mt-1">CTR {pct(t.ctr)}</div>
              </div>
              <div className="w-[60%]">
                <Bars data={lastWeek.map((d) => ({ label: new Date(d.date).toLocaleDateString("en", { weekday: "narrow" }), value: d.conversions }))} />
              </div>
            </div>
          </section>
          <section className="card">
            <div className="flex items-center justify-between"><span className="title">Pending approvals</span><Link href="/approvals" className="meta hover:underline">View all</Link></div>
            <div className="mt-2">
              {!approvals?.length && <div className="row"><span className="circle !h-8 !w-8"><Check size={14} /></span><span className="label">All clear</span></div>}
              {approvals?.slice(0, 4).map((a) => (
                <Link key={a.interrupt_id} href="/approvals" className="row">
                  <span className="circle !h-8 !w-8"><Inbox size={14} /></span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium">{a.type === "question" ? a.question : GATE_TITLES[a.gate ?? a.type] ?? a.gate}</div>
                    <div className="meta">{a.client}</div>
                  </div>
                  <span className="pill blue">{a.type === "gate" ? "review" : a.type.replace("_", " ")}</span>
                </Link>
              ))}
            </div>
          </section>
        </div>

        {/* ---------- right column ---------- */}
        <div className="flex min-w-0 flex-col gap-5">
          <section className="card">
            <span className="title">Next optimizer decision</span>
            {nextAction ? (
              <>
                <div className="tile mt-3 flex items-center gap-3">
                  <Avatar name="shlok.optimizer" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium">{nextAction.kind.replace("_", " ")}</div>
                    <div className="meta truncate">{nextAction.ref}</div>
                  </div>
                </div>
                <div className="tile mt-2">
                  <div className="label">{nextAction.change?.daily_to ? "New daily budget" : "Reason"}</div>
                  <div className={nextAction.change?.daily_to ? "hero-num !text-[30px] mt-1" : "text-[13px] mt-1"}>
                    {nextAction.change?.daily_to ? money(nextAction.change.daily_to, cur) : nextAction.reason}
                  </div>
                  {nextAction.change?.daily_to && <div className="meta mt-2">{nextAction.reason}</div>}
                </div>
                <div className="mt-2 flex items-center justify-between px-1 meta"><span>Applies to</span><span>Meta · instantly</span></div>
                <div className="mt-4 grid grid-cols-[1fr_auto] gap-2">
                  <button className="btn btn-primary" disabled={!!busy} onClick={() => act("ok", () => post(`/runs/${clientId}/actions/${nextAction.id}`, { approve: true }))}>Continue</button>
                  <button className="btn btn-ghost" disabled={!!busy} onClick={() => act("no", () => post(`/runs/${clientId}/actions/${nextAction.id}`, { approve: false }))}>Skip</button>
                </div>
              </>
            ) : (
              <p className="label mt-3">{live ? "No decisions waiting. The optimizer applies safe actions on its own and asks you for budget moves." : "Appears once ads are live."}</p>
            )}
          </section>

          <section className="card">
            <span className="title">Top creative</span>
            <div className="gradient-card mt-3 aspect-[1.6] p-4">
              {bestCreative && (bestCreative.kind === "image" || bestCreative.thumbnail_path) && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={fileUrl(bestCreative.kind === "image" ? bestCreative.path : bestCreative.thumbnail_path)} alt="" className="absolute inset-0 h-full w-full object-cover opacity-35 mix-blend-luminosity" />
              )}
              <div className="relative flex h-full flex-col justify-between">
                <span className="text-[15px] font-medium" style={{ color: "rgba(255,255,255,.85)", textShadow: "0 1px 2px rgba(0,0,0,.15)" }}>rune</span>
                <div>
                  <div className="text-[14px] font-medium leading-snug">{bestCreative?.ad_copy?.headline ?? "Your best ad shows here"}</div>
                  <div className="mt-1 flex items-center justify-between text-[11px] opacity-90">
                    <span>{best ? `CPA ${money(best[1].cpa, cur)} · CTR ${pct(best[1].ctr)}` : "after the first week live"}</span>
                    <span className="font-semibold tracking-wide">{bestCreative?.ratio ?? "META"}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function QA({ icon, label, href, onClick, disabled, busy }: { icon: React.ReactNode; label: string; href?: string; onClick?: () => void; disabled?: boolean; busy?: boolean }) {
  const inner = (
    <>
      <span className={`circle mx-auto ${busy ? "animate-pulse" : ""}`}>{icon}</span>
      <span className="label mt-1.5 block !text-[11px]">{label}</span>
    </>
  );
  return href ? <Link href={href}>{inner}</Link> : <button disabled={disabled || busy} onClick={onClick} className="disabled:opacity-40">{inner}</button>;
}

function Stat({ label, value, d, sub, invert }: { label: string; value: string; d: number | null; sub: string; invert?: boolean }) {
  const good = d != null && (invert ? d <= 0 : d >= 0);
  return (
    <section className="card !p-4">
      <div className="label">{label}</div>
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <span className="card-num">{value}</span>
        {d != null && <span className={`pill ${good ? "ok" : "bad"}`}>{d >= 0 ? "+" : ""}{(d * 100).toFixed(1)}%</span>}
      </div>
      <div className="meta mt-1">{sub}</div>
    </section>
  );
}
