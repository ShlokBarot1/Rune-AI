"use client";
import { CheckCircle2, Radio } from "lucide-react";
import { GATE_TITLES, money, pct, usePoll, type Json } from "@/lib/api";
import ApprovalDock from "@/components/rune/ApprovalDock";
import ReportLinks from "@/components/rune/ReportLinks";

function Campaign({ run }: { run: Json }) {
  const [snap, , reload] = usePoll<Json>(`/runs/${run.client_id}`, 5000);
  const [perf] = usePoll<Json>(run.live ? `/runs/${run.client_id}/performance` : null, 15000);
  const cur = snap?.values?.brief?.budget?.currency ?? "INR";
  const t = perf?.totals;
  const pending = snap?.pending ?? [];
  return (
    <section className="card flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-[18px] font-semibold">{run.name}</div>
          <div className="meta">{run.live ? "Ads are live" : pending.length ? "Waiting for you" : run.running ? "The agency is working on it" : "In preparation"}</div>
        </div>
        <span className={`pill ${run.live ? "ok" : pending.length ? "warn" : ""}`}>{run.live ? <><Radio size={11} /> live</> : pending.length ? `${pending.length} to review` : "in progress"}</span>
      </div>
      {t && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[["Spend", money(t.spend, cur)], ["Results", t.conversions], ["Cost per result", money(t.cpa, cur)], ["CTR", pct(t.ctr)]].map(([k, v]) => (
            <div key={k} className="tile"><div className="label">{k}</div><div className="text-[18px] font-semibold">{v}</div></div>
          ))}
        </div>
      )}
      {pending.map((p: Json) => (
        <div key={p.interrupt_id}>
          <div className="label mb-1">{p.type === "question" ? "A question for you" : `Review: ${GATE_TITLES[p.gate] ?? p.gate ?? "Go live"}`}</div>
          <ApprovalDock item={p} clientId={run.client_id} onDone={reload} />
        </div>
      ))}
      {!pending.length && !run.live && <div className="meta flex items-center gap-1.5"><CheckCircle2 size={13} /> Nothing needs you right now.</div>}
      {(run.live || snap?.values?.budget_plan) && <ReportLinks cid={run.client_id} />}
    </section>
  );
}

/** A client's home: their campaigns, what waits for them, results, reports. */
export default function Portal() {
  const [runs, err] = usePoll<Json[]>("/runs", 8000);
  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="label">Client portal</div>
        <h1 className="page-title mt-1">Your campaigns</h1>
      </div>
      {err && <div className="pill bad">{err}</div>}
      {runs && !runs.length && <div className="card meta">No campaigns are shared with you yet.</div>}
      {runs?.map((r) => <Campaign key={r.client_id} run={r} />)}
    </div>
  );
}
