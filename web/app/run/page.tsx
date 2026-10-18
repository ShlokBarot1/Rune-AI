"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Images, Plus, ShieldCheck } from "lucide-react";
import { money, msgText, useEvents, type Json } from "@/lib/api";
import { STAGES, canEdit, currentStage, stageStates, useRun, type StageId } from "@/lib/run";
import { PipelineRail, Theater } from "@/components/rune/Pipeline";
import { AuditSummary, BriefCard, HooksView, ResearchView, StrategyView } from "@/components/rune/Views";
import { CreativesBoard } from "@/components/rune/CreativesBoard";
import ApprovalDock from "@/components/rune/ApprovalDock";
import BriefEditor from "@/components/rune/BriefEditor";
import { BudgetView, MetaBuildView, TargetingView } from "@/components/rune/Launch";

function Stat({ label, value, sub, dark = false, href }: { label: string; value: React.ReactNode; sub?: string; dark?: boolean; href?: string }) {
  return (
    <div className={`card !p-5 flex flex-col justify-between gap-3 ${dark ? "!text-white" : ""}`} style={dark ? { background: "var(--ink)", borderColor: "transparent" } : undefined}>
      <div className="flex items-center justify-between">
        <span className="label" style={dark ? { color: "#9aa3b2" } : undefined}>{label}</span>
        {href && <Link href={href} className="circle sm" style={dark ? { background: "var(--accent)", color: "var(--accent-ink)", borderColor: "transparent" } : undefined}><ArrowUpRight size={14} /></Link>}
      </div>
      <div className="card-num">{value}</div>
      {sub && <div className="meta" style={dark ? { color: "#9aa3b2" } : undefined}>{sub}</div>}
    </div>
  );
}

function ShlokStage({ id, v, cid, gate, editable, reload }: { id: StageId; v: Json; cid: string | null; gate?: string; editable: boolean; reload: () => void }) {
  if (id === "budget") return <BudgetView v={v} />;
  if (id === "targeting") return <TargetingView v={v} cid={cid} editable={editable} atGate={gate === "targeting"} onSent={reload} />;
  if (id === "launch") return <MetaBuildView v={v} />;
  if ((id === "audit" || id === "go_live") && v.audit) return <AuditSummary a={v.audit} round={v.audit_round} />;
  return <div className="tile py-10 text-center text-[13px]" style={{ color: "var(--text-2)" }}>This stage hasn&apos;t run yet.</div>;
}

/** While generation runs: planned vs finished assets, each QA verdict as it lands (from the agent bus). */
function CreativesProgress({ msgs }: { msgs: Json[] }) {
  const logs = msgs.filter((m) => m.type === "LOG");
  const plan = [...logs].reverse().find((m) => m.sender === "malav.planner" && String(m.payload?.text).startsWith("Plan:"));
  const total = Number(String(plan?.payload?.text ?? "").match(/Plan: (\d+) assets/)?.[1] ?? 0);
  const since = plan ? plan.seq : 0;
  const results = logs.filter((m) => m.seq > since && m.sender === "malav.qa" && /: (pass|NEEDS HUMAN)/.test(m.payload?.text ?? ""));
  const steps = logs.filter((m) => m.seq > since && m.sender === "malav.generator");
  const step = steps[steps.length - 1]?.payload?.text;
  const done = results.length;
  return (
    <section className="card fade-in">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Generating creatives</div>
          <div className="hero-num mt-1">{done}<span className="text-[22px] opacity-40"> / {total || "…"}</span></div>
        </div>
        <span className="pill lime live-pulse">● {step ?? "planning the jobs…"}</span>
      </div>
      <div className="mt-4 h-2 rounded-full" style={{ background: "var(--bar)" }}>
        <div className="h-2 rounded-full transition-all duration-700" style={{ width: `${total ? (done / total) * 100 : 4}%`, background: "var(--ink)" }} />
      </div>
      <div className="mt-4 grid gap-2 md:grid-cols-2">
        {["Background scene", "Real product cut-out", "Text & CTA template", "Vision QA"].map((s, i) => (
          <div key={s} className="tile flex items-center gap-2 !py-2.5 text-[12.5px]">
            <span className="agent-dot !h-6 !w-6" style={{ background: i < 3 || done ? "var(--accent)" : "var(--bar)", color: "var(--accent-ink)" }}>{i + 1}</span>{s}
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-col">
        {results.slice(-8).reverse().map((m) => {
          const t = String(m.payload.text), ok = t.includes(": pass");
          return (
            <div key={m.seq} className="row fade-in !py-2">
              <span className={`pill ${ok ? "ok" : "bad"} shrink-0`}>{ok ? "QA pass" : "needs human"}</span>
              <span className="truncate text-[12.5px]">{t.split(":")[0]}</span>
              <span className="meta ml-auto shrink-0">{t.match(/score (\d+)/)?.[0]}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function MissionControl() {
  const { runs, clientId, setClientId, snap, reload } = useRun();
  const [picked, setPicked] = useState<StageId | null>(null);
  const [editing, setEditing] = useState(false);
  const msgs = useEvents(clientId, 400);
  const v = snap?.values ?? {};
  const states = stageStates(snap);
  const cur = currentStage(snap);
  const stage = picked ?? cur;
  useEffect(() => { setPicked(null); }, [cur]);  // follow the live stage when it moves on

  if (runs && !runs.length) return (
    <div className="card flex flex-col items-center gap-4 py-20 text-center">
      <div className="page-title">No campaigns yet</div>
      <p style={{ color: "var(--text-2)" }}>Paste a brand&apos;s website and watch the agents build a full Meta campaign.</p>
      <Link href="/onboarding" className="btn btn-lime"><Plus size={15} /> New campaign</Link>
    </div>
  );

  const pkg = v.package, pending = snap?.pending?.[0], editable = canEdit(snap);
  const done = STAGES.filter((s) => states[s.id] === "done").length;
  const nego = msgs.filter((m) => (m.sender === "malav.strategy" && m.recipient === "shlok.budget") || (m.sender === "shlok.budget" && m.recipient === "malav.strategy"));
  const negotiation = nego.length ? nego.map((m) => (
    <div key={m.seq} className={`bubble ${m.sender.startsWith("malav") ? "light-right" : "light-left"}`}>
      <div className="meta mb-0.5" style={m.sender.startsWith("malav") ? { color: "#9aa3b2" } : undefined}>{m.sender}</div>{msgText(m)}
    </div>
  )) : <div className="meta">No negotiation recorded yet.</div>;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Mission Control</div>
          <h1 className="page-title mt-1">{v.brief?.business_name ?? clientId ?? "…"}</h1>
        </div>
        <div className="flex items-center gap-2">
          {runs && runs.length > 1 && (
            <select className="input !w-auto !rounded-full !py-2" value={clientId ?? ""} onChange={(e) => setClientId(e.target.value)} aria-label="Campaign">
              {runs.map((r: Json) => <option key={r.client_id} value={r.client_id}>{r.name}</option>)}
            </select>
          )}
          <span className={`pill ${snap?.running ? "lime" : pending ? "warn" : v.live ? "ok" : ""} !px-3 !py-1.5 !text-[12px]`}>
            {snap?.running ? "● agents running" : pending ? "waiting for you" : v.live ? "live" : "idle"}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <Stat dark label="Pipeline" value={<>{done}<span className="text-[16px] opacity-50"> / {STAGES.length}</span></>} sub={`now: ${STAGES.find((s) => s.id === cur)?.label}`} />
        <Stat label="Budget" value={v.brief ? money(v.brief.budget?.total, v.brief.budget?.currency) : "—"} sub={v.brief ? `${v.brief.budget?.start} → ${v.brief.budget?.end}` : undefined} />
        <Stat label="Creatives" value={pkg?.creatives?.length ?? 0} sub={pkg ? `${pkg.creatives.filter((c: Json) => c.qa?.passed).length} passed vision QA` : "not generated yet"} href="/studio" />
        <Stat label="Audit" value={v.audit?.decision?.replace(/_/g, " ") ?? "—"} sub={v.audit ? `round ${v.audit_round ?? 1}` : "after the Meta build"} href="/audit" />
      </div>

      <div className="card !p-3"><PipelineRail states={states} selected={stage} onSelect={setPicked} /></div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-4">
          {pending && clientId && <ApprovalDock key={pending.interrupt_id} item={pending} clientId={clientId} onDone={reload} />}
          {stage === "intake" && (editing && v.brief && clientId
            ? <BriefEditor cid={clientId} brief={v.brief} atGate={pending?.gate === "intake"} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
            : <BriefCard b={v.brief ?? null} onEdit={editable ? () => setEditing(true) : undefined} />)}
          {stage === "research" && <ResearchView r={v.research ?? null} />}
          {stage === "strategy" && <StrategyView bp={v.blueprint ?? null} negotiation={negotiation} />}
          {stage === "hooks" && <HooksView hooks={v.hooks ?? null} bp={v.blueprint ?? null} brand={v.brief?.business_name ?? "Brand"} />}
          {stage === "creatives" && (pkg
            ? <CreativesBoard pkg={pkg} compact hooks={v.hooks ?? []} cid={clientId} editable={editable} atGate={pending?.gate === "creatives"} onChanged={reload} />
            : <CreativesProgress msgs={msgs} />)}
          {["budget", "targeting", "launch", "audit", "go_live"].includes(stage) && <ShlokStage id={stage} v={v} cid={clientId} gate={pending?.gate} editable={editable} reload={reload} />}
          {stage === "creatives" && pkg && <Link href="/studio" className="btn btn-ghost self-start"><Images size={15} /> Open Creative Studio</Link>}
          {stage === "audit" && v.audit && <Link href="/audit" className="btn btn-ghost self-start"><ShieldCheck size={15} /> Full audit report</Link>}
        </div>
        <div className="xl:sticky xl:top-6 xl:self-start"><Theater clientId={clientId} height="calc(100vh - 48px)" /></div>
      </div>
    </div>
  );
}
