"use client";
import Link from "next/link";
import { AlertTriangle, CircleCheck, Info, OctagonAlert, Wrench } from "lucide-react";
import { useEvents, type Json } from "@/lib/api";
import { useRun } from "@/lib/run";
import { AuditSummary } from "@/components/rune/Views";
import ApprovalDock from "@/components/rune/ApprovalDock";

const SEV: Record<string, [string, React.ReactNode]> = {
  blocker: ["bad", <OctagonAlert key="b" size={13} />],
  major: ["warn", <AlertTriangle key="m" size={13} />],
  minor: ["", <Info key="i" size={13} />],
};
const OWNER: Record<string, string> = { malav: "Create · Malav's agents", shlok: "Launch · Shlok's agents", human: "Needs you" };

export default function AuditReport() {
  const { clientId, snap, reload } = useRun(4000);
  const msgs = useEvents(clientId, 600);
  const a = snap?.values?.audit;
  const pending = snap?.pending?.[0];

  // fix loop: each auditor verdict, with the fix prompts it sent
  const timeline: { decision: string; scores?: Json; n: number; fixes: Json[] }[] = [];
  for (const m of msgs) {
    if (m.sender === "auditor" && m.type === "INFORM" && m.payload?.decision)
      timeline.push({ decision: m.payload.decision, scores: m.payload.scores, n: m.payload.n_findings, fixes: [] });
    if (m.sender === "auditor" && m.type === "FIX_PROMPT" && timeline.length) timeline[timeline.length - 1].fixes.push(m);
  }

  if (!a) return (
    <div className="card flex flex-col items-center gap-3 py-16 text-center">
      <div className="page-title">No audit yet</div>
      <p className="meta">The Auditor runs after the Meta build, before anything can go live.</p>
      <Link href="/run" className="btn btn-primary">Watch Mission Control</Link>
    </div>
  );

  const byOwner: Record<string, Json[]> = {};
  for (const f of a.findings ?? []) (byOwner[f.owner] ??= []).push(f);

  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="label">Audit Report</div>
        <h1 className="page-title mt-1">{snap?.values?.brief?.business_name ?? clientId}</h1>
      </div>
      {pending?.type === "go_live" && clientId && <ApprovalDock key={pending.interrupt_id} item={pending} clientId={clientId} onDone={reload} />}
      <AuditSummary a={a} round={snap?.values?.audit_round} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          {Object.keys(byOwner).length === 0 && (
            <section className="card flex items-center gap-3"><CircleCheck style={{ color: "var(--success)" }} /> No findings — clean pass.</section>
          )}
          {Object.entries(byOwner).map(([owner, fs]) => (
            <section key={owner} className="card">
              <div className="flex items-center justify-between">
                <div className="title">{OWNER[owner] ?? owner}</div><span className="pill">{fs.length}</span>
              </div>
              <div className="mt-2">
                {fs.map((f, i) => (
                  <div key={i} className="row !items-start">
                    <span className={`pill ${SEV[f.severity]?.[0] ?? ""} shrink-0`}>{SEV[f.severity]?.[1]} {f.severity}</span>
                    <div className="min-w-0">
                      <div className="text-[13px] leading-snug">{f.message}</div>
                      <div className="meta mt-0.5">{f.dimension} · {f.object_ref}</div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>

        <section className="panel-dark self-start">
          <div className="label">Fix loop</div>
          <div className="text-[17px] font-semibold tracking-tight">Audit → fix → re-audit</div>
          <ol className="mt-4 flex flex-col gap-4">
            {timeline.map((t, i) => (
              <li key={i} className="flex gap-3">
                <span className="agent-dot" style={{ background: t.decision === "FAIL" ? "#ff8a80" : "var(--accent)", color: "#111" }}>{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <b>Round {i + 1}</b><span className={`pill ${t.decision === "FAIL" ? "bad" : "lime"}`}>{t.decision.replace(/_/g, " ")}</span>
                    <span className="meta">{t.n} findings</span>
                  </div>
                  {t.fixes.map((f: Json) => (
                    <div key={f.seq} className="tile mt-2 text-[12px]">
                      <div className="flex items-center gap-1.5 font-semibold"><Wrench size={12} /> fix prompt → {f.recipient}</div>
                      <div className="mt-1 opacity-70 line-clamp-3 whitespace-pre-line">{f.payload?.prompt}</div>
                    </div>
                  ))}
                </div>
              </li>
            ))}
            {!timeline.length && <li className="meta">The audit history appears here as rounds complete.</li>}
          </ol>
        </section>
      </div>
    </div>
  );
}
