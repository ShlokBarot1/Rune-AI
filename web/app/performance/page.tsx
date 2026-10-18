"use client";
import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, Check, FastForward, Orbit, X } from "lucide-react";
import Bars from "@/components/Bars";
import Stat from "@/components/Stat";
import { ClientPicker, useClient } from "@/components/useClient";
import { fileUrl, money, pct, post, useEvents, usePoll, type Json } from "@/lib/api";

const ACTION_LABEL: Record<string, string> = {
  pause_ad: "Paused ad", scale_adset: "Budget change", cut_adset: "Stop ad set",
  refresh_creatives: "Asked for new creatives", launch_creatives: "Launch new creatives",
};

export default function Performance() {
  const { runs, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 4000);
  const live = !!run?.values?.live;
  const [perf, , reload] = usePoll<Json>(clientId && live ? `/runs/${clientId}/performance` : null, 5000);
  const msgs = useEvents(clientId, 600);
  const [report, setReport] = useState("");
  const [busy, setBusy] = useState(false);

  const v = run?.values ?? {};
  const cur = v.brief?.budget?.currency ?? "INR";
  const t = perf?.totals ?? {};
  const target = perf?.plan?.target_cpa ?? v.brief?.kpi_targets?.target_cpa;
  const creatives: Record<string, Json> = Object.fromEntries((v.package?.creatives ?? []).map((c: Json) => [c.creative_id, c]));
  const paused = new Set((perf?.actions ?? []).filter((a: Json) => a.kind === "pause_ad" && a.status === "applied").map((a: Json) => a.ref));

  // the optimizer agent's latest cycle: its LOG steps after the last monitor alert batch
  const optLogs = msgs.filter((m) => m.sender === "shlok.optimizer" && m.type === "LOG");
  const cycleStart = [...optLogs].reverse().find((m) => m.payload?.step === 1)?.seq ?? 0;
  const reasoning = optLogs.filter((m) => m.seq >= cycleStart);

  const cycle = async () => {
    setBusy(true);
    try { setReport((await post(`/runs/${clientId}/cycle`, { day: perf?.daily?.length ?? 0, days: 7 })).report); }
    finally { setBusy(false); reload(); }
  };
  const decide = async (id: string, approve: boolean) => { await post(`/runs/${clientId}/actions/${id}`, { approve }); reload(); };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Launch &amp; optimise</div>
          <h1 className="page-title mt-1">Live Performance</h1>
        </div>
        <div className="flex items-center gap-2">
          <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
          <button className={`btn btn-lime ${busy ? "live-pulse" : ""}`} disabled={!live || busy} onClick={cycle}>
            <FastForward size={15} /> {busy ? "Optimizer agent working…" : "Simulate next 7 days"}
          </button>
        </div>
      </div>

      {!live && (
        <div className="card flex flex-wrap items-center gap-3">
          <span className="title flex-1">This campaign isn&apos;t live yet — finish the approvals and click Go live.</span>
          <Link href="/run" className="btn btn-ghost"><Orbit size={15} /> Mission Control</Link>
        </div>
      )}

      {live && (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            <Stat dark label="Spend" value={money(t.spend, cur)} sub={`of ${money(perf?.plan?.total, cur)} · reserve ${money(perf?.plan?.testing_reserve, cur)}/day`} />
            <Stat label="Conversions" value={t.conversions ?? 0} sub={`CTR ${pct(t.ctr)}`} />
            <Stat label="Cost per conversion" value={money(t.cpa, cur)} sub={target ? (t.cpa && t.cpa <= target ? `beating the ${money(target, cur)} target` : `target ${money(target, cur)}`) : "no target set"} />
            <Stat label="ROAS" value={(t.roas ?? 0).toFixed(2)} sub={`${money(t.revenue, cur)} revenue`} />
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
            <div className="flex min-w-0 flex-col gap-4">
              <section className="card">
                <div className="flex items-center justify-between"><span className="label">Daily spend</span><span className="meta">{perf?.daily?.length ?? 0} days monitored</span></div>
                <div className="mt-4">
                  <Bars height={150} format={(n) => money(n, cur)}
                    data={(perf?.daily ?? []).slice(-14).map((d: Json) => ({ label: String(new Date(d.date).getDate()), value: d.spend }))} />
                </div>
              </section>

              <section className="card">
                <div className="flex items-center justify-between">
                  <span className="label">Optimizer actions</span>
                  <span className="meta">safe actions run on their own · money moves wait for you</span>
                </div>
                <div className="mt-2 flex flex-col">
                  {!perf?.actions?.length && <div className="meta py-3">Run a week to let the optimizer agent act.</div>}
                  {[...(perf?.actions ?? [])].reverse().map((a: Json) => (
                    <div key={a.id} className="row flex-wrap !items-start">
                      <span className={`pill ${a.risk === "auto" ? "dark" : "lime"} shrink-0`}>{a.risk === "auto" ? "auto" : "needs you"}</span>
                      <div className="min-w-[220px] flex-1">
                        <div className="text-[13px] font-semibold">{ACTION_LABEL[a.kind] ?? a.kind} <span className="meta font-normal">· {a.ref}</span></div>
                        <div className="text-[12.5px]" style={{ color: "var(--text-2)" }}>
                          {a.change?.daily_to ? <b style={{ color: "var(--text)" }}>{money(a.change.daily_from, cur)} → {money(a.change.daily_to, cur)}/day. </b> : null}{a.reason}
                        </div>
                      </div>
                      {a.status === "proposed" ? (
                        <span className="flex gap-2">
                          <button className="btn btn-lime !h-9" onClick={() => decide(a.id, true)}><Check size={14} /> Approve</button>
                          <button className="btn btn-ghost !h-9" onClick={() => decide(a.id, false)} aria-label="Reject"><X size={14} /></button>
                        </span>
                      ) : <span className={`pill ${a.status === "applied" ? "ok" : "bad"}`}>{a.status}</span>}
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <div className="flex flex-col gap-4">
              <section className="panel-dark flex flex-col" style={{ maxHeight: 560 }}>
                <div className="flex items-center justify-between pb-3">
                  <div>
                    <div className="label">Latest cycle</div>
                    <div className="text-[17px] font-semibold tracking-tight">Optimizer reasoning</div>
                  </div>
                  <span className={`pill ${busy ? "lime" : "dark"}`}>{busy ? "● thinking" : `${reasoning.length} steps`}</span>
                </div>
                <div className="scroll-thin -mr-2 flex-1 overflow-y-auto pr-2">
                  {!reasoning.length && <p className="meta py-6 text-center">Run a week to watch the agent investigate and act.</p>}
                  {reasoning.map((m) => {
                    const p = m.payload ?? {}, bad = String(p.observation ?? "").startsWith("REJECTED");
                    return (
                      <div key={m.seq} className="feed-line fade-in">
                        <span className="agent-dot" style={{ background: "#9fb7ff", color: "#0d1a3d" }}>{p.step ?? "✓"}</span>
                        <div className="min-w-0">
                          <div style={{ color: "var(--night-text)" }}>{p.text}</div>
                          {p.tool && (
                            <div className="mt-1 flex flex-wrap items-start gap-1.5">
                              <span className={`pill ${p.tool.startsWith("get_") ? "dark" : "lime"}`}>{p.tool}</span>
                              <span className="text-[11.5px]" style={{ color: bad ? "#ff9b91" : "var(--night-text-2)" }}>{String(p.observation ?? "").slice(0, 140)}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>
              <section className="card">
                <div className="label flex items-center gap-1.5"><AlertTriangle size={12} /> Monitor alerts</div>
                <div className="mt-2 max-h-56 overflow-y-auto">
                  {!perf?.alerts?.length && <div className="meta py-3">No alerts.</div>}
                  {perf?.alerts?.map((a: Json, i: number) => (
                    <div key={i} className="row !py-2">
                      <span className={`pill ${a.severity === "critical" ? "bad" : a.severity === "warn" ? "warn" : "ok"} shrink-0`}>{a.kind.replace("_", " ")}</span>
                      <span className="text-[12.5px]">{a.message}</span>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          </div>

          {report && (
            <section className="card fade-in">
              <div className="label">Daily report · written by shlok.report</div>
              <p className="mt-2 whitespace-pre-wrap text-[13.5px] leading-relaxed">{report}</p>
            </section>
          )}

          <section className="card overflow-x-auto">
            <span className="label">Ads · best cost per conversion first</span>
            <table className="data mt-2 min-w-[760px]">
              <thead><tr><th>Ad</th><th>Ad set</th><th>Status</th><th>Spend</th><th>CTR</th><th>Conv.</th><th>CPA</th><th>Freq.</th><th>Hook rate</th></tr></thead>
              <tbody>
                {Object.entries<Json>(perf?.stats ?? {}).sort((a, b) => (a[1].cpa ?? 1e9) - (b[1].cpa ?? 1e9)).map(([ref, s]) => {
                  const c = creatives[ref], thumb = c && (c.kind === "image" ? c.path : c.thumbnail_path);
                  return (
                    <tr key={ref}>
                      <td>
                        <div className="flex items-center gap-3">
                          {thumb ? <img src={fileUrl(thumb)} alt="" className="h-11 w-9 rounded-lg object-cover" style={{ background: "var(--bar)" }} /> : <span className="h-11 w-9 rounded-lg" style={{ background: "var(--bar)" }} />}
                          <div><div className="font-semibold">{c?.ad_copy?.headline ?? ref}</div><div className="meta">{c?.ratio} · {c?.kind}</div></div>
                        </div>
                      </td>
                      <td className="meta">{s.adset_ref}</td>
                      <td><span className={`pill ${paused.has(ref) ? "bad" : "ok"}`}>{paused.has(ref) ? "paused" : "active"}</span></td>
                      <td>{money(s.spend, cur)}</td>
                      <td>{pct(s.ctr)}</td>
                      <td>{s.conversions}</td>
                      <td style={{ color: target && s.cpa ? (s.cpa <= target ? "var(--success)" : "var(--danger)") : undefined, fontWeight: 600 }}>{money(s.cpa, cur)}</td>
                      <td>{s.frequency?.toFixed(1)}</td>
                      <td>{s.hook_rate != null ? pct(s.hook_rate, 0) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}
