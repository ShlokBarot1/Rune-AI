"use client";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft, ChevronRight, ClipboardList, Compass, Hand, Image as ImageIcon, Layers, Loader2, MessagesSquare,
  Quote, Rocket, Search, ShieldCheck, Target, Timer, Wallet,
} from "lucide-react";
import { money, useEvents, type BusMsg, type Json } from "@/lib/api";
import { STAGES, agentColor, agentName, agentShort, stageStates, useRun, type StageId, type StageState } from "@/lib/run";
import { Feed, isWorking } from "@/components/rune/Pipeline";

const ICON: Record<StageId, typeof Search> = {
  intake: ClipboardList, research: Search, strategy: Compass, hooks: Quote, creatives: ImageIcon,
  budget: Wallet, targeting: Target, launch: Layers, audit: ShieldCheck, go_live: Rocket,
};
const TEAMS = [
  { id: "malav", name: "Creative studio", who: "Malav's agents", stages: ["intake", "research", "strategy", "hooks", "creatives"] },
  { id: "shlok", name: "Launch desk", who: "Shlok's agents", stages: ["budget", "targeting", "launch"] },
  { id: "auditor", name: "Quality gate", who: "Auditor", stages: ["audit"] },
  { id: "human", name: "Your call", who: "You", stages: ["go_live"] },
] as const;
const CREW: Partial<Record<StageId, string[]>> = {
  research: ["audience", "competitors", "trends"],
  creatives: ["planner", "generator", "qa", "packager"],
};

/** Which chapter a bus message belongs to: by its agent, or by who it talks to (a reply to strategy is strategy). */
function stageOfMsg(m: BusMsg): StageId | null {
  const of = (a: string): StageId | null => {
    const [team, role = ""] = a.split(".");
    if (team === "malav") {
      if (role === "research") return "research";
      if (["planner", "generator", "qa", "packager", "studio", "creatives"].includes(role)) return "creatives";
      if (["intake", "strategy", "hooks"].includes(role)) return role as StageId;
      if (role === "feedback") return "go_live";
      return null;
    }
    if (team === "shlok") return role === "launch" ? "launch" : (["budget", "targeting"].includes(role) ? role as StageId : "go_live");
    if (team === "auditor") return "audit";
    return (STAGES.find((s) => s.id === a)?.id as StageId) ?? null;
  };
  if (m.sender === "human") return of(m.recipient);
  return (m.recipient.startsWith("malav.") ? of(m.recipient) : null) ?? of(m.sender);
}

/** What each chapter produced, in one line. */
function outcome(id: StageId, v: Json): string | null {
  const n = (x: unknown) => (Array.isArray(x) ? x.length : 0);
  const adsets = (v.blueprint?.campaigns ?? []).flatMap((c: Json) => c.adsets);
  switch (id) {
    case "intake": return v.brief ? `${v.brief.business_name} · ${money(v.brief.budget?.total, v.brief.budget?.currency)} · ${v.brief.goal}` : null;
    case "research": return v.research ? `${n(v.research.personas)} personas · ${n(v.research.competitors)} competitors · ${n(v.research.trends)} trends` : null;
    case "strategy": return v.blueprint ? `${n(v.blueprint.campaigns)} campaign · ${adsets.length} ad sets` : null;
    case "hooks": return v.hooks ? `${n(v.hooks)} hooks written and scored` : null;
    case "creatives": return v.package ? `${n(v.package.creatives)} assets · ${v.package.creatives.filter((c: Json) => c.qa?.passed).length} passed vision QA` : null;
    case "budget": return v.budget_plan ? `${money(Object.values<number>(v.budget_plan.adset_daily ?? {}).reduce((a, b) => a + b, 0), v.budget_plan.currency)}/day across ${Object.keys(v.budget_plan.adset_daily ?? {}).length} ad sets` : null;
    case "targeting": return v.targeting ? `${n(v.targeting)} audiences built` : null;
    case "launch": return v.launch ? `${v.launch.objects.filter((o: Json) => o.kind === "ad").length} ads built, all paused` : null;
    case "audit": return v.audit ? `${String(v.audit.decision).replace(/_/g, " ")} · round ${v.audit_round ?? 1}` : null;
    case "go_live": return v.live ? "Ads are live" : null;
  }
}

const secs = (ms: BusMsg[]) => ms.length < 2 ? 0 : Math.round((new Date(ms[ms.length - 1].created_at).getTime() - new Date(ms[0].created_at).getTime()) / 1000);
const dur = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`);
const STATE_TEXT: Record<StageState, string> = { done: "done", running: "working", waiting: "needs you", todo: "up next" };

function Station({ id, st, steps, on, onClick }: { id: StageId; st: StageState; steps: number; on: boolean; onClick: () => void }) {
  const Icon = ICON[id];
  const stage = STAGES.find((s) => s.id === id)!;
  const [bg, fg] = agentColor(stage.owner);
  const ring = st === "running" ? "var(--accent)" : st === "waiting" ? "#ffcf7a" : st === "done" ? bg : "var(--night-3)";
  return (
    <button onClick={onClick} className="group flex w-[84px] shrink-0 flex-col items-center gap-2 text-center">
      <span className={`relative grid h-14 w-14 place-items-center rounded-full transition ${st === "running" ? "live-pulse" : ""} ${on ? "scale-110" : "group-hover:scale-105"}`}
        style={{ background: st === "todo" ? "var(--night-2)" : on ? ring : "var(--night-2)", color: on && st !== "todo" ? (st === "done" ? fg : "#1a1300") : st === "todo" ? "var(--night-text-2)" : ring,
          boxShadow: `0 0 0 2px ${ring}${on ? ", 0 0 28px " + ring : ""}` }}>
        {st === "running" ? <Loader2 size={20} className="animate-spin" /> : st === "waiting" ? <Hand size={19} /> : <Icon size={20} />}
        {steps > 0 && <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full px-1 text-[10px] font-bold" style={{ background: "var(--night-text)", color: "var(--night)" }}>{steps}</span>}
      </span>
      <span className="text-[12.5px] font-semibold leading-tight" style={{ color: on ? "#fff" : "var(--night-text)" }}>{stage.label}</span>
      <span className="text-[10.5px]" style={{ color: st === "waiting" ? "#ffcf7a" : st === "running" ? "var(--accent)" : "var(--night-text-2)" }}>{STATE_TEXT[st]}</span>
    </button>
  );
}

/** The line into a station: dim before it starts, lit once reached, marching lime while it works. */
const Wire = ({ st, team = false }: { st: StageState; team?: boolean }) => (
  <span className={`wire ${st === "running" ? "running" : st === "todo" ? "" : "lit"} ${team ? "self-center" : "mt-7 !w-4 !min-w-4 !flex-none self-start"}`} />
);

export default function TheaterPage() {
  const { runs, clientId, setClientId, snap } = useRun(2500);
  const msgs = useEvents(clientId, 600);
  const v = snap?.values ?? {};
  const states = stageStates(snap);
  const working = isWorking(msgs);

  // every message lands in one chapter; agentless ones (core, evaluator) stay with the chapter before them
  const chapters = useMemo(() => {
    const out = Object.fromEntries(STAGES.map((s) => [s.id, [] as BusMsg[]])) as Record<StageId, BusMsg[]>;
    let cur: StageId = "intake";
    for (const m of msgs) { cur = stageOfMsg(m) ?? cur; out[cur].push(m); }
    return out;
  }, [msgs]);
  // open on the station that's working; if it has nothing to show yet (go-live waiting), the last one that does
  const active = STAGES.find((s) => states[s.id] === "running" || states[s.id] === "waiting")?.id;
  const live = (active && chapters[active].length ? active
    : [...STAGES].reverse().find((s) => chapters[s.id].length)?.id ?? "intake") as StageId;
  const [picked, setPicked] = useState<StageId | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => { setPicked(null); }, [clientId]);
  const sel = picked ?? live;
  const idx = STAGES.findIndex((s) => s.id === sel);
  const stage = STAGES[idx];
  const shown = all ? msgs : chapters[sel];

  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [shown.length, sel, all]);

  const steps = shown.filter((m) => m.type === "LOG").length;
  const talks = shown.filter((m) => m.sender !== "human" && m.recipient !== "theater" && m.recipient !== "human" && m.type !== "LOG");
  const yours = shown.filter((m) => m.sender === "human");
  const crew = Array.from(new Set(shown.filter((m) => m.sender !== "human" && !m.sender.startsWith("core")).map((m) => m.sender)));
  const [bg, fg] = agentColor(stage.owner);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Agent Theater</div>
          <h1 className="page-title mt-1">Watch the agency work</h1>
          <p className="mt-2 text-[13.5px]" style={{ color: "var(--text-2)" }}>Ten agents, one campaign. Pick a station to replay what it thought, who it talked to and what you decided.</p>
        </div>
        <div className="flex items-center gap-2">
          {runs && runs.length > 1 && (
            <select className="input !w-auto !rounded-full !py-2" value={clientId ?? ""} onChange={(e) => setClientId(e.target.value)} aria-label="Campaign">
              {runs.map((r: Json) => <option key={r.client_id} value={r.client_id}>{r.name}</option>)}
            </select>
          )}
          <span className={`pill ${working ? "lime live-pulse" : ""} !px-3 !py-1.5 !text-[12px]`}>{working ? "● agents working" : "idle"}</span>
        </div>
      </div>

      {/* the agency floor: four teams, ten stations, wires light up as work flows */}
      <section className="panel-dark !p-6 overflow-x-auto scroll-thin">
        <div className="flex min-w-[1080px] items-stretch gap-2">
          {TEAMS.map((t, ti) => {
            const [tbg] = agentColor(t.id);
            return (
              <Fragment key={t.id}>
                {ti > 0 && <Wire team st={states[t.stages[0] as StageId]} />}
                <div className="flex flex-col gap-4 rounded-[22px] px-3 py-4" style={{ background: "rgba(255,255,255,.03)", border: "1px solid rgba(255,255,255,.06)", flex: "0 0 auto" }}>
                  <div className="px-1 leading-tight">
                    <div className="flex items-center gap-1.5 whitespace-nowrap text-[12px] font-semibold" style={{ color: "var(--night-text)" }}>
                      <span className="h-2 w-2 rounded-full" style={{ background: tbg }} />{t.name}
                    </div>
                    <div className="mt-0.5 pl-3.5 text-[11px]" style={{ color: "var(--night-text-2)" }}>{t.who}</div>
                  </div>
                  <div className="flex items-start gap-1">
                    {t.stages.map((id, i) => (
                      <Fragment key={id}>
                        {i > 0 && <Wire st={states[id as StageId]} />}
                        <Station id={id as StageId} st={states[id as StageId]} steps={chapters[id as StageId].length} on={!all && sel === id}
                          onClick={() => { setAll(false); setPicked(id as StageId); }} />
                      </Fragment>
                    ))}
                  </div>
                </div>
              </Fragment>
            );
          })}
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        {/* chapter summary */}
        <section className="card flex flex-col gap-4 self-start xl:sticky xl:top-6">
          <div className="flex items-center justify-between">
            <div className="seg">
              <button className={!all ? "on" : ""} onClick={() => setAll(false)}>Chapter</button>
              <button className={all ? "on" : ""} onClick={() => setAll(true)}>Whole run</button>
            </div>
            {!all && (
              <div className="flex gap-1.5">
                <button className="circle sm" disabled={idx === 0} onClick={() => setPicked(STAGES[idx - 1].id)} aria-label="Previous chapter"><ChevronLeft size={15} /></button>
                <button className="circle sm" disabled={idx === STAGES.length - 1} onClick={() => setPicked(STAGES[idx + 1].id)} aria-label="Next chapter"><ChevronRight size={15} /></button>
              </div>
            )}
          </div>

          {all ? (
            <div>
              <div className="label">The whole run</div>
              <div className="mt-1 text-[22px] font-semibold tracking-tight">{v.brief?.business_name ?? "Campaign"}</div>
              <div className="meta mt-1">{STAGES.filter((s) => states[s.id] === "done").length} of {STAGES.length} stations done</div>
            </div>
          ) : (
            <div className="flex items-start gap-3">
              <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl" style={{ background: bg, color: fg }}>
                {(() => { const I = ICON[sel]; return <I size={20} />; })()}
              </span>
              <div className="min-w-0">
                <div className="label">Chapter {idx + 1} · {stage.owner === "human" ? "you" : `${stage.owner}'s ${stage.owner === "auditor" ? "check" : "agents"}`}</div>
                <div className="text-[22px] font-semibold leading-tight tracking-tight">{stage.label}</div>
                <span className={`pill mt-1.5 ${states[sel] === "done" ? "ok" : states[sel] === "waiting" ? "warn" : states[sel] === "running" ? "lime" : ""}`}>{STATE_TEXT[states[sel]]}</span>
              </div>
            </div>
          )}

          {!all && (
            <div className="tile">
              <div className="label">Outcome</div>
              <div className="mt-1 text-[14px] font-semibold leading-snug">{outcome(sel, v) ?? "Not produced yet"}</div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-2">
            {[[Timer, dur(secs(shown)), "thinking"], [Layers, steps, "steps"], [MessagesSquare, talks.length, "messages"]].map(([I, n, l]) => {
              const Ic = I as typeof Timer;
              return (
                <div key={String(l)} className="tile !p-3">
                  <Ic size={13} style={{ color: "var(--text-3)" }} />
                  <div className="mt-1 text-[18px] font-semibold tabular-nums">{String(n)}</div>
                  <div className="meta">{String(l)}</div>
                </div>
              );
            })}
          </div>

          {crew.length > 0 && (
            <div>
              <div className="label mb-2">Who worked on it</div>
              <div className="flex flex-wrap gap-1.5">
                {crew.map((a) => { const [b, f] = agentColor(a); return (
                  <span key={a} className="inline-flex items-center gap-1.5 rounded-full py-1 pl-1 pr-2.5 text-[11.5px] font-semibold" style={{ background: "var(--tile)" }}>
                    <span className="grid h-5 w-5 place-items-center rounded-full text-[9px]" style={{ background: b === "#ffffff" ? "var(--ink)" : b, color: b === "#ffffff" ? "#fff" : f }}>{agentShort(a)}</span>{agentName(a)}
                  </span>
                ); })}
                {!all && CREW[sel]?.filter((c) => !crew.some((a) => a.endsWith(c))).map((c) => <span key={c} className="pill">{c}</span>)}
              </div>
            </div>
          )}

          {yours.length > 0 && (
            <div>
              <div className="label mb-2">Your decisions</div>
              <div className="flex flex-col gap-1.5">
                {yours.map((m) => (
                  <div key={m.seq} className="flex items-start gap-2 text-[12.5px]">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: "var(--ink)" }} />
                    <span>{m.payload?.answer != null ? `Answered “${m.payload.answer}”` : m.payload?.action === "approve" ? `Approved ${m.recipient.replace("malav.", "")}` :
                      m.payload?.action === "changes" ? `Asked for changes: “${m.payload.comment}”` : m.payload?.action === "reject" ? "Asked to redo it" : String(m.payload?.action ?? "Replied")}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* the transcript */}
        <section className="panel-dark flex flex-col" style={{ height: "calc(100vh - 48px)", minHeight: 520 }}>
          <div className="flex items-center justify-between pb-3">
            <div>
              <div className="label">{all ? "Full transcript" : `Chapter ${idx + 1} transcript`}</div>
              <div className="text-[17px] font-semibold tracking-tight">{all ? "Everything, in order" : `Inside ${stage.label.toLowerCase()}`}</div>
            </div>
            <span className="pill dark">{shown.length} events</span>
          </div>
          <div ref={box} className="scroll-thin -mr-2 flex-1 overflow-y-auto pr-2">
            {shown.length === 0
              ? <p className="meta py-16 text-center">{states[sel] === "todo" ? "This station hasn't started yet." : "Nothing recorded here."}</p>
              : <Feed msgs={shown} working={working && (all || sel === live)} keepOpen={all ? 6 : 999} />}
          </div>
        </section>
      </div>
    </div>
  );
}
