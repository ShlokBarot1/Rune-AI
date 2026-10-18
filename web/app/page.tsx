"use client";
import Link from "next/link";
import { useState } from "react";
import { BarChart3, Check, FastForward, Hand, Inbox, Plus, TrendingUp } from "lucide-react";
import Avatar from "@/components/Avatar";
import Bars from "@/components/Bars";
import Stat from "@/components/Stat";
import { Theater } from "@/components/rune/Pipeline";
import { PhoneFrame } from "@/components/rune/Views";
import { ClientPicker, useClient } from "@/components/useClient";
import { GATE_TITLES, money, pct, post, usePoll, type Json } from "@/lib/api";

export default function CommandCenter() {
  const { runs, err, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 3000);
  const live = !!run?.values?.live;
  const [perf, , reloadPerf] = usePoll<Json>(clientId && live ? `/runs/${clientId}/performance` : null, 5000);
  const [approvals] = usePoll<Json[]>("/approvals", 3000);
  const [busy, setBusy] = useState("");

  const v = run?.values ?? {};
  const cur = v.brief?.budget?.currency ?? "INR";
  const total = v.budget_plan?.total ?? v.brief?.budget?.total ?? 0;
  const t = perf?.totals ?? {};
  const daily: Json[] = perf?.daily ?? [];
  const lastWeek = daily.slice(-7), prevWeek = daily.slice(-14, -7);
  const sum = (xs: Json[], k: string) => xs.reduce((a, d) => a + (d[k] ?? 0), 0);
  const delta = (k: string) => { const a = sum(lastWeek, k), b = sum(prevWeek, k); return b ? (a - b) / b : null; };
  const nextAction = perf?.actions?.find((a: Json) => a.risk === "approval" && a.status === "proposed");
  const best = Object.entries<Json>(perf?.stats ?? {}).sort((a, b) => (a[1].cpa ?? 1e9) - (b[1].cpa ?? 1e9))[0];
  const bestCreative = best && v.package?.creatives?.find((c: Json) => c.creative_id === best[0]);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try { await fn(); } finally { setBusy(""); reloadPerf(); }
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Launch &amp; optimise</div>
          <h1 className="page-title mt-1">Command Center</h1>
        </div>
        <div className="flex items-center gap-2">
          <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
          <span className={`pill ${live ? "lime" : run?.pending?.length ? "warn" : ""} !px-3 !py-1.5 !text-[12px]`}>
            {live ? "● live · monitored" : run?.pending?.length ? "waiting for you" : run?.running ? "agents running" : "not live yet"}
          </span>
        </div>
      </div>
      {err && <div className="card label">API unreachable ({err}). Run <code>uvicorn api:app --port 8000</code> in backend/.</div>}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat dark label="Budget remaining" value={money(total - (t.spend ?? 0), cur)} href="/performance"
          sub={`spent ${money(t.spend ?? 0, cur)} of ${money(total, cur)}`} />
        <Stat label="Revenue" value={money(t.revenue, cur)} delta={delta("revenue")} sub={`ROAS ${(t.roas ?? 0).toFixed(2)}`} />
        <Stat label="Ad spend" value={money(t.spend, cur)} delta={delta("spend")} invert sub={`CPA ${money(t.cpa, cur)}`} />
        <Stat label="Conversions · 7 days" value={sum(lastWeek, "conversions")} delta={delta("conversions")} sub={`CTR ${pct(t.ctr)}`} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-4">
          {/* optimizer decision dock (same shape as the approval dock) */}
          <section className="card !p-5" style={{ background: "rgba(255,255,255,.92)" }}>
            <div className="flex items-start gap-3">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: nextAction ? "var(--accent)" : "var(--bar)", color: "var(--accent-ink)" }}>
                {nextAction ? <Hand size={16} /> : <Check size={16} />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="label">{nextAction ? "The optimizer agent needs you" : "Optimizer agent"}</div>
                {nextAction ? (
                  <>
                    <div className="text-[16px] font-semibold leading-snug">
                      {nextAction.kind === "scale_adset" ? `Change ${nextAction.ref} budget` : nextAction.kind === "cut_adset" ? `Stop ad set ${nextAction.ref}` : `Launch new creatives in ${nextAction.ref}`}
                    </div>
                    {nextAction.change?.daily_to && (
                      <div className="mt-2 flex items-baseline gap-2">
                        <span className="meta line-through">{money(nextAction.change.daily_from, cur)}</span>
                        <span className="hero-num !text-[32px]">{money(nextAction.change.daily_to, cur)}</span><span className="label">/day</span>
                      </div>
                    )}
                    <p className="tile mt-2 text-[13px] leading-relaxed">{nextAction.reason}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button className="btn btn-lime" disabled={!!busy} onClick={() => act("ok", () => post(`/runs/${clientId}/actions/${nextAction.id}`, { approve: true }))}><Check size={15} /> Approve</button>
                      <button className="btn btn-ghost" disabled={!!busy} onClick={() => act("no", () => post(`/runs/${clientId}/actions/${nextAction.id}`, { approve: false }))}>Skip</button>
                      <Link href="/performance" className="btn btn-ghost"><BarChart3 size={15} /> See the numbers</Link>
                    </div>
                  </>
                ) : (
                  <p className="mt-1 text-[13px]" style={{ color: "var(--text-2)" }}>
                    {live ? "Nothing waiting. It pauses losing ads on its own and asks you before moving money." : "Starts working once the campaign is live."}
                  </p>
                )}
              </div>
              {live && (
                <button className="btn btn-primary shrink-0" disabled={!!busy}
                  onClick={() => act("cycle", () => post(`/runs/${clientId}/cycle`, { day: daily.length, days: 7 }))}>
                  <FastForward size={15} /> {busy === "cycle" ? "Agents working…" : "Next week"}
                </button>
              )}
            </div>
          </section>

          <div className="grid gap-4 md:grid-cols-[1.25fr_1fr]">
            <section className="card">
              <div className="flex items-center justify-between">
                <span className="label flex items-center gap-1.5"><TrendingUp size={12} /> Conversions · last 7 days</span>
                <Link href="/performance" className="meta hover:underline">Details</Link>
              </div>
              <div className="mt-4">
                <Bars height={120} data={lastWeek.map((d) => ({ label: new Date(d.date).toLocaleDateString("en", { weekday: "narrow" }), value: d.conversions }))} />
              </div>
              {!lastWeek.length && <p className="meta mt-2">Appears after the first monitored week.</p>}
            </section>

            <section className="card">
              <div className="flex items-center justify-between">
                <span className="label flex items-center gap-1.5"><Inbox size={12} /> Waiting for you</span>
                <Link href="/approvals" className="meta hover:underline">All</Link>
              </div>
              <div className="mt-2">
                {!approvals?.length && <div className="row"><span className="circle sm"><Check size={14} /></span><span className="label">All clear</span></div>}
                {approvals?.slice(0, 4).map((a) => (
                  <Link key={a.interrupt_id} href="/approvals" className="row">
                    <Avatar name={a.agent ?? (["budget", "targeting"].includes(a.gate) ? "shlok.budget" : a.type === "go_live" ? "auditor" : "malav.agent")} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold">{a.type === "question" ? a.question : GATE_TITLES[a.gate ?? a.type] ?? a.gate}</div>
                      <div className="meta">{a.client}</div>
                    </div>
                    <span className="pill lime">{a.type === "gate" ? "review" : a.type.replace("_", " ")}</span>
                  </Link>
                ))}
              </div>
            </section>
          </div>

          <section className="card">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="label">Top creative · lowest cost per conversion</span>
              {best && <span className="pill ok">CPA {money(best[1].cpa, cur)} · CTR {pct(best[1].ctr)}</span>}
            </div>
            {bestCreative ? (
              <div className="mt-4 flex flex-wrap items-center gap-6">
                <PhoneFrame c={bestCreative} width={170} />
                <div className="min-w-[200px] flex-1">
                  <div className="text-[18px] font-semibold leading-snug">{bestCreative.ad_copy?.headline}</div>
                  <p className="mt-1 text-[13px]" style={{ color: "var(--text-2)" }}>{bestCreative.ad_copy?.primary_text}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <span className="pill dark">{bestCreative.ratio}</span><span className="pill">{bestCreative.kind}</span>
                    <span className="pill blue">{bestCreative.adset_ref}</span>
                    <span className="pill">{best[1].conversions} conversions</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="tile mt-3 py-8 text-center text-[13px]" style={{ color: "var(--text-2)" }}>
                Your best-performing ad shows here after the first week live.{" "}
                {!runs?.length && <Link href="/onboarding" className="font-semibold underline"><Plus size={12} className="inline" /> Start a campaign</Link>}
              </div>
            )}
          </section>
        </div>
        <div className="xl:sticky xl:top-6 xl:self-start"><Theater clientId={clientId} height={720} /></div>
      </div>
    </div>
  );
}
