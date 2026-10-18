"use client";
import { useState } from "react";
import { FastForward } from "lucide-react";
import Bars from "@/components/Bars";
import { ClientPicker, useClient } from "@/components/useClient";
import { money, pct, post, usePoll, type Json } from "@/lib/api";

export default function Performance() {
  const { runs, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 4000);
  const live = !!run?.values?.live;
  const [perf, , reload] = usePoll<Json>(clientId && live ? `/runs/${clientId}/performance` : null, 5000);
  const [report, setReport] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const v = run?.values ?? {};
  const cur = v.brief?.budget?.currency ?? "INR";
  const t = perf?.totals ?? {};
  const creatives: Record<string, Json> = Object.fromEntries((v.package?.creatives ?? []).map((c: Json) => [c.creative_id, c]));
  const paused = new Set((perf?.actions ?? []).filter((a: Json) => a.kind === "pause_ad" && a.status === "applied").map((a: Json) => a.ref));
  const target = perf?.plan?.target_cpa ?? v.brief?.kpi_targets?.target_cpa;

  const cycle = async () => {
    setBusy(true);
    try {
      const out = await post(`/runs/${clientId}/cycle`, { day: perf?.daily?.length ?? 0, days: 7 });
      setReport(out.report);
    } finally {
      setBusy(false);
      reload();
    }
  };
  const decide = async (id: string, approve: boolean) => {
    await post(`/runs/${clientId}/actions/${id}`, { approve });
    reload();
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] tracking-tight" style={{ fontWeight: 500 }}>Live Performance</h1>
          <p className="label mt-1">Monitor → optimizer → feedback to the creative agents. Safe actions run on their own; budget moves wait for you.</p>
        </div>
        <div className="flex items-center gap-2">
          <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
          <button className="btn btn-primary" disabled={!live || busy} onClick={cycle}><FastForward size={15} /> {busy ? "Agents working…" : "Simulate next 7 days"}</button>
        </div>
      </div>

      {!live && <div className="card label">This client isn’t live yet. Finish the approvals and click Go live.</div>}

      {live && (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Kpi label="Spend" value={money(t.spend, cur)} sub={`of ${money(perf?.plan?.total, cur)}`} />
            <Kpi label="Conversions" value={String(t.conversions ?? 0)} />
            <Kpi label="CPA" value={money(t.cpa, cur)} sub={target ? `target ${money(target, cur)}` : undefined} tone={target && t.cpa ? (t.cpa <= target ? "ok" : "bad") : undefined} />
            <Kpi label="ROAS" value={(t.roas ?? 0).toFixed(2)} />
            <Kpi label="CTR" value={pct(t.ctr)} />
          </div>

          <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
            <section className="card">
              <div className="flex items-center justify-between"><span className="title">Daily spend</span><span className="meta">{perf?.daily?.length ?? 0} days</span></div>
              <div className="mt-4">
                <Bars height={150} format={(n) => money(n, cur)}
                  data={(perf?.daily ?? []).slice(-14).map((d: Json) => ({ label: new Date(d.date).getDate().toString(), value: d.spend }))} />
              </div>
            </section>
            <section className="card">
              <span className="title">Alerts</span>
              <div className="mt-2 max-h-56 overflow-y-auto">
                {!perf?.alerts?.length && <div className="meta py-3">No alerts.</div>}
                {perf?.alerts?.map((a: Json, i: number) => (
                  <div key={i} className="row">
                    <span className={`pill ${a.severity === "critical" ? "bad" : a.severity === "warn" ? "warn" : "ok"}`}>{a.kind.replace("_", " ")}</span>
                    <span className="flex-1 text-[12.5px]">{a.message}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <section className="card">
            <span className="title">Optimizer actions</span>
            <div className="mt-2">
              {!perf?.actions?.length && <div className="meta py-3">Run a cycle to let the optimizer act.</div>}
              {[...(perf?.actions ?? [])].reverse().map((a: Json) => (
                <div key={a.id} className="row flex-wrap">
                  <span className={`pill ${a.risk === "auto" ? "" : "blue"}`}>{a.risk === "auto" ? "auto" : "needs you"}</span>
                  <span className="text-[13px] font-medium">{a.kind.replace("_", " ")}</span>
                  <span className="meta">{a.ref}</span>
                  <span className="min-w-[200px] flex-1 text-[12.5px]" style={{ color: "var(--text-2)" }}>
                    {a.reason}{a.change?.daily_to ? ` → ${money(a.change.daily_from, cur)} to ${money(a.change.daily_to, cur)}/day` : ""}
                  </span>
                  {a.status === "proposed" ? (
                    <span className="flex gap-2">
                      <button className="btn btn-primary !h-8" onClick={() => decide(a.id, true)}>Approve</button>
                      <button className="btn btn-ghost !h-8" onClick={() => decide(a.id, false)}>Reject</button>
                    </span>
                  ) : (
                    <span className={`pill ${a.status === "applied" ? "ok" : "bad"}`}>{a.status}</span>
                  )}
                </div>
              ))}
            </div>
          </section>

          {report && (
            <section className="card fade-in">
              <span className="title">Daily report</span>
              <p className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed">{report}</p>
            </section>
          )}

          <section className="card overflow-x-auto">
            <span className="title">Ads</span>
            <table className="data mt-2 min-w-[720px]">
              <thead><tr><th>Ad</th><th>Ad set</th><th>Status</th><th>Spend</th><th>CTR</th><th>Conv.</th><th>CPA</th><th>Freq.</th><th>Hook rate</th></tr></thead>
              <tbody>
                {Object.entries<Json>(perf?.stats ?? {}).sort((a, b) => (a[1].cpa ?? 1e9) - (b[1].cpa ?? 1e9)).map(([ref, s]) => (
                  <tr key={ref}>
                    <td><div className="font-medium">{creatives[ref]?.ad_copy?.headline ?? ref}</div><div className="meta">{creatives[ref]?.ratio} · {creatives[ref]?.kind}</div></td>
                    <td className="meta">{s.adset_ref}</td>
                    <td><span className={`pill ${paused.has(ref) ? "bad" : "ok"}`}>{paused.has(ref) ? "paused" : "active"}</span></td>
                    <td>{money(s.spend, cur)}</td>
                    <td>{pct(s.ctr)}</td>
                    <td>{s.conversions}</td>
                    <td style={{ color: target && s.cpa ? (s.cpa <= target ? "var(--success)" : "var(--danger)") : undefined }}>{money(s.cpa, cur)}</td>
                    <td>{s.frequency?.toFixed(1)}</td>
                    <td>{s.hook_rate != null ? pct(s.hook_rate, 0) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "ok" | "bad" }) {
  return (
    <section className="card !p-4">
      <div className="label">{label}</div>
      <div className="card-num mt-3" style={{ color: tone === "ok" ? "var(--success)" : tone === "bad" ? "var(--danger)" : undefined }}>{value}</div>
      {sub && <div className="meta mt-0.5">{sub}</div>}
    </section>
  );
}
