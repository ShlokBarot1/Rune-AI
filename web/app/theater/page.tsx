"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, Bot, Brush, Building2, Clapperboard, Compass, Crosshair, Crown, FileBarChart, Megaphone, Handshake, Hammer, Layers,
  Lightbulb, MessagesSquare, Minus, PackageCheck, Palette, PenTool, Plus, Repeat2, RotateCcw, Scale, ScanEye, ShieldCheck,
  SlidersHorizontal, Swords, Target, Telescope, Timer, TrendingUp, UserRound, Users, Wallet, X, type LucideIcon,
} from "lucide-react";
import { money, msgText, useEvents, type BusMsg, type Json } from "@/lib/api";
import { STAGES, stageStates, useRun } from "@/lib/run";
import { Feed } from "@/components/rune/Pipeline";

/* ---------------- the agency: who reports to whom ---------------- */
type Team = "malav" | "shlok" | "auditor" | "human" | "rune";
type Agent = { id: string; title: string; job: string; team: Team; parent?: string; stage?: string; kind: "root" | "client" | "dept" | "agent" | "sub" };

const ORG: Agent[] = [
  { id: "rune", title: "Agency director", job: "Runs the whole campaign: hands work from one department to the next and stops at every approval.", team: "rune", kind: "root" },
  { id: "human", title: "You · the client", job: "Answers questions, approves every stage and gives the final go-live.", team: "human", kind: "client" },

  { id: "dept.client", title: "Client services", job: "Owns the relationship and the brief.", team: "malav", parent: "rune", kind: "dept" },
  { id: "malav.intake", title: "Account manager", job: "Reads your website, asks only what's missing and writes the client brief.", team: "malav", parent: "dept.client", stage: "intake", kind: "agent" },

  { id: "dept.strategy", title: "Strategy & research", job: "Finds who to sell to and how to structure the campaign.", team: "malav", parent: "rune", kind: "dept" },
  { id: "malav.research", title: "Head of research", job: "Runs three researchers in parallel and synthesises personas, competitors and trends.", team: "malav", parent: "dept.strategy", stage: "research", kind: "agent" },
  { id: "malav.research.audience", title: "Audience researcher", job: "Mines reviews and forums for pains, desires and real customer language.", team: "malav", parent: "malav.research", kind: "sub" },
  { id: "malav.research.competitors", title: "Competitive analyst", job: "Finds competitors and reads their live ads in the Meta Ad Library.", team: "malav", parent: "malav.research", kind: "sub" },
  { id: "malav.research.trends", title: "Trend scout", job: "Seasonal moments and Google Trends peaks inside the flight.", team: "malav", parent: "malav.research", kind: "sub" },
  { id: "malav.strategy", title: "Media strategist", job: "Turns research into campaigns and ad sets; negotiates feasibility with the media planner.", team: "malav", parent: "dept.strategy", stage: "strategy", kind: "agent" },

  { id: "dept.creative", title: "Creative", job: "Writes the hooks and produces every ad asset.", team: "malav", parent: "rune", kind: "dept" },
  { id: "malav.hooks", title: "Copywriter", job: "Writes hooks and ad copy per ad set, scored by a critic and checked for policy.", team: "malav", parent: "dept.creative", stage: "hooks", kind: "agent" },
  { id: "malav.creatives", title: "Creative director", job: "Plans every asset, has it produced and signs off after vision QA.", team: "malav", parent: "dept.creative", stage: "creatives", kind: "agent" },
  { id: "malav.planner", title: "Producer", job: "Plans which asset each ad needs, in which ratio and format.", team: "malav", parent: "malav.creatives", kind: "sub" },
  { id: "malav.generator", title: "Designer", job: "Scene, product cut-out, layout, video and voiceover.", team: "malav", parent: "malav.creatives", kind: "sub" },
  { id: "malav.qa", title: "Creative QA", job: "Checks every asset with vision: text, product, safe zones, contrast.", team: "malav", parent: "malav.creatives", kind: "sub" },
  { id: "malav.packager", title: "Trafficker", job: "Packages assets with copy and tracked landing URLs.", team: "malav", parent: "malav.creatives", kind: "sub" },
  { id: "malav.fixer", title: "Iteration team", job: "Fixes what the auditor flags and makes new versions of winning ads.", team: "malav", parent: "dept.creative", kind: "agent" },

  { id: "dept.media", title: "Media buying", job: "Budgets, audiences and the build on Meta.", team: "shlok", parent: "rune", kind: "dept" },
  { id: "shlok.budget", title: "Media planner", job: "Splits the budget across ad sets and keeps each above Meta's learning minimums.", team: "shlok", parent: "dept.media", stage: "budget", kind: "agent" },
  { id: "shlok.targeting", title: "Audience buyer", job: "Turns each persona into a Meta audience with real interest IDs.", team: "shlok", parent: "dept.media", stage: "targeting", kind: "agent" },
  { id: "shlok.launch", title: "Campaign builder", job: "Builds campaigns, ad sets and ads on Meta, all paused until you go live.", team: "shlok", parent: "dept.media", stage: "launch", kind: "agent" },

  { id: "dept.performance", title: "Performance", job: "Watches live results and optimises.", team: "shlok", parent: "rune", kind: "dept" },
  { id: "shlok.monitor", title: "Performance analyst", job: "Pulls live numbers and spots fatigue or waste.", team: "shlok", parent: "dept.performance", kind: "agent" },
  { id: "shlok.optimizer", title: "Optimiser", job: "Proposes budget moves and pauses; risky ones wait for you.", team: "shlok", parent: "dept.performance", kind: "agent" },
  { id: "shlok.feedback", title: "Feedback loop", job: "Sends winners and fatigue signals back to Creative.", team: "shlok", parent: "dept.performance", kind: "agent" },
  { id: "shlok.report", title: "Reporting", job: "Writes your performance reports.", team: "shlok", parent: "dept.performance", kind: "agent" },

  { id: "dept.quality", title: "Quality & compliance", job: "Nothing ships without passing review.", team: "auditor", parent: "rune", kind: "dept" },
  { id: "auditor", title: "Auditor", job: "Scores brief, creatives, budget, targeting and the Meta build; sends fixes to the owners.", team: "auditor", parent: "dept.quality", stage: "audit", kind: "agent" },
  { id: "core.suggestions", title: "Change reviewer", job: "Checks your change requests against policy, budget and the brief before agents apply them.", team: "auditor", parent: "dept.quality", kind: "agent" },
];
const BY_ID = Object.fromEntries(ORG.map((a) => [a.id, a]));

/** Every job gets its own mark. */
const ICONS: Record<string, LucideIcon> = {
  rune: Crown, human: UserRound,
  "dept.client": Handshake, "dept.strategy": Lightbulb, "dept.creative": Palette, "dept.media": Megaphone,
  "dept.performance": Activity, "dept.quality": ShieldCheck,
  "malav.intake": Handshake, "malav.research": Telescope, "malav.research.audience": Users, "malav.research.competitors": Swords,
  "malav.research.trends": TrendingUp, "malav.strategy": Compass, "malav.hooks": PenTool, "malav.creatives": Clapperboard,
  "malav.planner": Layers, "malav.generator": Brush, "malav.qa": ScanEye, "malav.packager": PackageCheck, "malav.fixer": Repeat2,
  "shlok.budget": Wallet, "shlok.targeting": Target, "shlok.launch": Hammer, "shlok.monitor": Activity,
  "shlok.optimizer": SlidersHorizontal, "shlok.feedback": Repeat2, "shlok.report": FileBarChart,
  auditor: ShieldCheck, "core.suggestions": Scale,
};
const Glyph = ({ a, size }: { a: Agent; size: number }) => {
  const I = ICONS[a.id] ?? (a.kind === "dept" ? Building2 : Bot);
  return <I size={size} strokeWidth={2.2} />;
};
const OWNER: Partial<Record<Team, string>> = { malav: "Malav's agents", shlok: "Shlok's agents", auditor: "Auditor" };
const TEAM_COLOR: Record<Team, string> = { malav: "#d4f34a", shlok: "#9fb7ff", auditor: "#ffcf7a", human: "#ffffff", rune: "#d4f34a" };

/** Agents the bus knows but the chart doesn't: hang them under their team's nearest department. */
function withUnknown(senders: string[]): Agent[] {
  const extra = senders.filter((s) => !BY_ID[s] && s !== "theater" && s !== "human" && !s.startsWith("core") && s.includes(".")).map((s): Agent => {
    const team = s.split(".")[0] as Team;
    const parent = team === "shlok" ? "dept.performance" : team === "auditor" ? "dept.quality" : "dept.creative";
    return { id: s, title: s.split(".").slice(1).join(" "), job: "Agent", team, parent, kind: "agent" };
  });
  return [...ORG, ...extra];
}

/* ---------------- layout: departments as columns, staff stacked beneath ---------------- */
const SIZE = { root: [300, 96], client: [240, 96], dept: [244, 72], agent: [244, 66], sub: [220, 54] } as const;
const COL = 272;
type Box = { x: number; y: number; w: number; h: number };

function layout(org: Agent[]): Record<string, Box> {
  const out: Record<string, Box> = {};
  const depts = org.filter((a) => a.kind === "dept");
  const width = depts.length * COL - 30;
  out.rune = { x: width / 2 - SIZE.root[0] / 2, y: 0, w: SIZE.root[0], h: SIZE.root[1] };
  out.human = { x: out.rune.x + SIZE.root[0] + 90, y: 0, w: SIZE.client[0], h: SIZE.client[1] };
  depts.forEach((d, i) => {
    const x = i * COL;
    out[d.id] = { x, y: 200, w: SIZE.dept[0], h: SIZE.dept[1] };
    let y = 200 + SIZE.dept[1] + 26;
    for (const a of org.filter((o) => o.parent === d.id)) {
      out[a.id] = { x: x + 14, y, w: SIZE.agent[0] - 14, h: SIZE.agent[1] };
      y += SIZE.agent[1] + 12;
      for (const s of org.filter((o) => o.parent === a.id)) {
        out[s.id] = { x: x + 38, y, w: SIZE.sub[0] - 14, h: SIZE.sub[1] };
        y += SIZE.sub[1] + 10;
      }
    }
  });
  return out;
}

/* ---------------- who did what ---------------- */
const talkOf = (m: BusMsg) => m.recipient !== "theater" && m.type !== "LOG";

function outcome(stage: string | undefined, v: Json): string | null {
  const n = (x: unknown) => (Array.isArray(x) ? x.length : 0);
  const adsets = (v.blueprint?.campaigns ?? []).flatMap((c: Json) => c.adsets);
  switch (stage) {
    case "intake": return v.brief ? `Brief for ${v.brief.business_name} · ${money(v.brief.budget?.total, v.brief.budget?.currency)} · goal: ${v.brief.goal}` : null;
    case "research": return v.research ? `${n(v.research.personas)} personas · ${n(v.research.competitors)} competitors · ${n(v.research.trends)} trends` : null;
    case "strategy": return v.blueprint ? `${n(v.blueprint.campaigns)} campaign · ${adsets.length} ad sets` : null;
    case "hooks": return v.hooks ? `${n(v.hooks)} hooks written and scored` : null;
    case "creatives": return v.package ? `${n(v.package.creatives)} assets · ${v.package.creatives.filter((c: Json) => c.qa?.passed).length} passed vision QA` : null;
    case "budget": return v.budget_plan ? `${money(Object.values<number>(v.budget_plan.adset_daily ?? {}).reduce((a, b) => a + b, 0), v.budget_plan.currency)}/day across ${Object.keys(v.budget_plan.adset_daily ?? {}).length} ad sets` : null;
    case "targeting": return v.targeting ? `${n(v.targeting)} audiences built` : null;
    case "launch": return v.launch ? `${v.launch.objects.filter((o: Json) => o.kind === "ad").length} ads built on Meta, all paused` : null;
    case "audit": return v.audit ? `${String(v.audit.decision).replace(/_/g, " ")} · round ${v.audit_round ?? 1}` : null;
  }
  return null;
}

const dur = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`);

/* ---------------- page ---------------- */
export default function AgencyTheater() {
  const { runs, clientId, setClientId, snap } = useRun(2500);
  const msgs = useEvents(clientId, 800);
  const v = snap?.values ?? {};
  const states = stageStates(snap);
  const org = useMemo(() => withUnknown(Array.from(new Set(msgs.flatMap((m) => [m.sender, m.recipient])))), [msgs]);
  const ids = useMemo(() => new Set(org.map((a) => a.id)), [org]);
  const base = useMemo(() => layout(org), [org]);
  const [moved, setMoved] = useState<Record<string, { dx: number; dy: number }>>({});
  const box = (id: string): Box => { const b = base[id], d = moved[id]; return d ? { ...b, x: b.x + d.dx, y: b.y + d.dy } : b; };
  const [view, setView] = useState({ x: 40, y: 40, k: 0.8 });
  const [sel, setSel] = useState<string | null>(null);
  const [talks, setTalks] = useState(true);
  const [grabbing, setGrabbing] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const now = Date.now();

  // messages per agent; a manager sees everything under them
  const own = useMemo(() => {
    const o: Record<string, BusMsg[]> = {};
    for (const m of msgs) for (const who of new Set([m.sender, m.recipient])) if (ids.has(who)) (o[who] ??= []).push(m);
    return o;
  }, [msgs, ids]);
  const tree = useCallback((id: string): BusMsg[] => {
    if (id === "rune") return msgs;
    const all = new Map<number, BusMsg>();
    const walk = (x: string) => { (own[x] ?? []).forEach((m) => all.set(m.seq, m)); org.filter((a) => a.parent === x).forEach((a) => walk(a.id)); };
    walk(id);
    return [...all.values()].sort((a, b) => a.seq - b.seq);
  }, [msgs, own, org]);
  const status = (id: string) => {
    const ms = tree(id);
    const last = ms[ms.length - 1];
    if (last && now - new Date(last.created_at).getTime() < 20000) return "working";
    if (id === "human" && snap?.pending?.length) return "waiting";
    return ms.length ? "done" : "idle";
  };

  // fit the chart to the canvas once per campaign
  const fit = useCallback(() => {
    const el = stage.current; if (!el) return;
    const bs = Object.values(base);
    const w = Math.max(...bs.map((b) => b.x + b.w)), h = Math.max(...bs.map((b) => b.y + b.h));
    const k = Math.min(1.1, (el.clientWidth - 60) / w, (el.clientHeight - 90) / h);
    setView({ k, x: (el.clientWidth - w * k) / 2, y: 30 });
  }, [base]);
  useEffect(() => { const t = setTimeout(fit, 60); return () => clearTimeout(t); }, [clientId]); // eslint-disable-line react-hooks/exhaustive-deps

  // wheel zooms around the cursor (non-passive so the page doesn't scroll)
  useEffect(() => {
    const el = stage.current; if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if ((e.target as Element).closest("aside")) return;  // let the profile panel scroll
      e.preventDefault();
      const r = el.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      setView((vw) => {
        const k = Math.min(2, Math.max(0.3, vw.k * (e.deltaY < 0 ? 1.1 : 0.9)));
        return { k, x: px - ((px - vw.x) * k) / vw.k, y: py - ((py - vw.y) * k) / vw.k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // grab the canvas to pan, grab a card to move it; a card that barely moved was clicked
  const drag = useRef<{ id: string | null; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null>(null);
  const down = (e: React.PointerEvent, id: string | null) => {
    e.stopPropagation();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const d = id ? moved[id] ?? { dx: 0, dy: 0 } : null;
    drag.current = { id, sx: e.clientX, sy: e.clientY, ox: d ? d.dx : view.x, oy: d ? d.dy : view.y, moved: false };
    if (!id) setGrabbing(true);
  };
  const move = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (d.id) { const id = d.id; setMoved((m) => ({ ...m, [id]: { dx: d.ox + dx / view.k, dy: d.oy + dy / view.k } })); }
    else setView((vw) => ({ ...vw, x: d.ox + dx, y: d.oy + dy }));
  };
  const up = () => {
    const d = drag.current; drag.current = null; setGrabbing(false);
    if (d && !d.moved) setSel(d.id);
  };

  // reporting lines + conversations between agents
  const lines = org.filter((a) => a.parent && base[a.parent]).map((a) => {
    const p = box(a.parent!), c = box(a.id);
    if (a.kind === "dept") {
      const sx = p.x + p.w / 2, sy = p.y + p.h, tx = c.x + c.w / 2, ty = c.y, my = (sy + ty) / 2;
      return { id: a.id, d: `M${sx},${sy} C${sx},${my} ${tx},${my} ${tx},${ty}` };
    }
    const sx = p.x + 18, sy = p.y + p.h, tx = c.x, ty = c.y + c.h / 2;
    return { id: a.id, d: `M${sx},${sy} C${sx},${ty} ${sx},${ty} ${tx},${ty}` };
  });
  const pairs = useMemo(() => {
    const p: Record<string, { a: string; b: string; n: number; last: number }> = {};
    for (const m of msgs) {
      if (!talkOf(m)) continue;
      const [a, b] = [m.sender, m.recipient].sort();
      if (!ids.has(a) || !ids.has(b) || a === b) continue;
      const k = `${a}|${b}`;
      p[k] = { a, b, n: (p[k]?.n ?? 0) + 1, last: new Date(m.created_at).getTime() };
    }
    return Object.values(p);
  }, [msgs, ids]);
  const done = STAGES.filter((s) => states[s.id] === "done").length;
  const rune = box("rune"), you = box("human");

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Agent Theater</div>
          <h1 className="page-title mt-1">Your AI agency, at work</h1>
          <p className="mt-2 text-[13.5px]" style={{ color: "var(--text-2)" }}>Every department and agent: who reports to whom and who talked to whom. Drag to move around, scroll to zoom, click anyone to see their work.</p>
        </div>
        <div className="flex items-center gap-2">
          {runs && runs.length > 1 && (
            <select className="input !w-auto !rounded-full !py-2" value={clientId ?? ""} onChange={(e) => setClientId(e.target.value)} aria-label="Campaign">
              {runs.map((r: Json) => <option key={r.client_id} value={r.client_id}>{r.name}</option>)}
            </select>
          )}
          <span className={`pill ${status("rune") === "working" ? "lime live-pulse" : ""} !px-3 !py-1.5 !text-[12px]`}>{status("rune") === "working" ? "● agency working" : `${done}/${STAGES.length} stages done`}</span>
        </div>
      </div>

      <section ref={stage} className="panel-dark !p-0 relative overflow-hidden select-none touch-none"
        style={{ height: "calc(100vh - 190px)", minHeight: 560, cursor: grabbing ? "grabbing" : "grab", backgroundColor: "var(--night)",
          backgroundImage: "radial-gradient(rgba(255,255,255,.08) 1px, transparent 1px)", backgroundSize: `${22 * view.k}px ${22 * view.k}px`, backgroundPosition: `${view.x}px ${view.y}px` }}
        onPointerDown={(e) => down(e, null)} onPointerMove={move} onPointerUp={up}>
        <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
          <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width="1" height="1">
            {lines.map((l) => <path key={l.id} d={l.d} fill="none" stroke="rgba(255,255,255,.18)" strokeWidth={1.5} />)}
            <path d={`M${rune.x + rune.w},${rune.y + rune.h / 2} L${you.x},${you.y + you.h / 2}`} stroke="rgba(255,255,255,.5)" strokeDasharray="5 6" strokeWidth={1.5} fill="none" />
            {talks && pairs.map((p) => {
              const a = box(p.a), b = box(p.b);
              const ax = a.x + a.w, ay = a.y + a.h / 2, bx = b.x + b.w, by = b.y + b.h / 2;
              const bend = 50 + Math.abs(ay - by) * 0.12 + Math.abs(ax - bx) * 0.06;
              const hot = now - p.last < 20000;
              return (
                <g key={p.a + p.b}>
                  <path d={`M${ax},${ay} C${ax + bend},${ay} ${bx + bend},${by} ${bx},${by}`} fill="none" stroke={hot ? "#d4f34a" : "rgba(212,243,74,.5)"}
                    strokeWidth={hot ? 2.2 : 1.4} strokeDasharray="4 6" className={hot ? "dash-run" : undefined} />
                  <text x={(ax + bx) / 2 + bend * 0.78} y={(ay + by) / 2 + 4} fill="#d4f34a" fontSize="11" fontWeight="700" textAnchor="middle">{p.n}</text>
                </g>
              );
            })}
          </svg>
          {org.map((a) => (
            <Node key={a.id} a={a} b={box(a.id)} st={status(a.id)} count={tree(a.id).length} on={sel === a.id}
              line={a.id === "rune" ? (v.brief?.business_name ? `Running ${v.brief.business_name} · ${done}/${STAGES.length} stages` : "No campaign yet") : lastLine(tree(a.id))}
              onDown={(e) => down(e, a.id)} />
          ))}
        </div>

        <div className="absolute bottom-4 left-4 flex items-center gap-1.5" onPointerDown={(e) => e.stopPropagation()}>
          {([[Plus, "Zoom in", () => setView((vw) => ({ ...vw, k: Math.min(2, vw.k * 1.15) }))],
            [Minus, "Zoom out", () => setView((vw) => ({ ...vw, k: Math.max(0.3, vw.k / 1.15) }))],
            [Crosshair, "Fit to screen", fit],
            [RotateCcw, "Reset layout", () => { setMoved({}); fit(); }]] as const).map(([Ic, label, fn]) => (
            <button key={label} title={label} aria-label={label} onClick={fn} className="grid h-9 w-9 place-items-center rounded-full" style={{ background: "var(--night-3)", color: "var(--night-text)" }}><Ic size={15} /></button>
          ))}
          <button onClick={() => setTalks(!talks)} className="ml-1 flex h-9 items-center gap-1.5 rounded-full px-3 text-[12px] font-semibold"
            style={{ background: talks ? "var(--accent)" : "var(--night-3)", color: talks ? "var(--accent-ink)" : "var(--night-text)" }}>
            <MessagesSquare size={13} /> Conversations
          </button>
        </div>
        {!sel && (
          <div className="pointer-events-none absolute bottom-5 right-5 flex flex-wrap gap-3 text-[11px]" style={{ color: "var(--night-text-2)" }}>
            {(["malav", "shlok", "auditor"] as Team[]).map((t) => <span key={t} className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full" style={{ background: TEAM_COLOR[t] }} />{OWNER[t]}</span>)}
            <span className="flex items-center gap-1.5"><span className="w-4 border-t border-dashed" style={{ borderColor: "#d4f34a" }} />talked (messages)</span>
          </div>
        )}

        {sel && ids.has(sel) && <Profile a={org.find((x) => x.id === sel)!} msgs={tree(sel)} v={v} st={status(sel)} org={org} onPick={setSel} onClose={() => setSel(null)} />}
      </section>
    </div>
  );
}

function lastLine(ms: BusMsg[]): string {
  const m = [...ms].reverse().find((x) => x.type === "LOG" || x.recipient === "theater") ?? ms[ms.length - 1];
  return m ? msgText(m) : "Not needed yet";
}

const STATUS: Record<string, [string, string]> = { working: ["#d4f34a", "working now"], waiting: ["#ffcf7a", "needs you"], done: ["#5fd38d", "worked on this campaign"], idle: ["#4a5263", "not needed yet"] };

function Node({ a, b, st, count, on, line, onDown }: { a: Agent; b: Box; st: string; count: number; on: boolean; line: string; onDown: (e: React.PointerEvent) => void }) {
  const color = TEAM_COLOR[a.team];
  const big = a.kind === "root" || a.kind === "client";
  const head = a.kind === "dept";
  const idle = st === "idle" && !head && !big;
  const av = big ? 48 : a.kind === "sub" ? 28 : 36;
  return (
    <div onPointerDown={onDown} className={`absolute flex cursor-pointer items-center gap-3 rounded-[18px] px-3 ${st === "working" ? "live-pulse" : ""}`}
      style={{ left: b.x, top: b.y, width: b.w, height: b.h, opacity: idle ? 0.55 : 1,
        background: a.kind === "root" ? "linear-gradient(135deg,#26331a,#141a22)" : head ? "rgba(255,255,255,.07)" : a.kind === "client" ? "#f4f6f9" : "var(--night-2)",
        border: `1px solid ${on ? color : head ? "rgba(255,255,255,.12)" : "rgba(255,255,255,.06)"}`,
        boxShadow: on ? `0 0 0 2px ${color}, 0 0 30px ${color}55` : "0 6px 18px rgba(0,0,0,.25)", color: a.kind === "client" ? "var(--text)" : "var(--night-text)" }}>
      {!head && (
        <span className="relative grid shrink-0 place-items-center rounded-full font-bold"
          style={{ width: av, height: av, fontSize: big ? 13 : a.kind === "sub" ? 10 : 12, background: a.kind === "client" ? "var(--ink)" : color, color: a.kind === "client" ? "#fff" : "#141a0a" }}>
          <Glyph a={a} size={Math.round(av * 0.46)} />
          <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full" style={{ background: STATUS[st][0], border: `2px solid ${a.kind === "client" ? "#f4f6f9" : "var(--night-2)"}` }} />
        </span>
      )}
      <div className="min-w-0 flex-1 leading-tight">
        <div className="flex items-center gap-1.5">
          {head && <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg" style={{ background: color, color: "#141a0a" }}><Glyph a={a} size={13} /></span>}
          <span className={`truncate font-semibold ${big ? "text-[15px]" : a.kind === "sub" ? "text-[12px]" : "text-[13px]"}`}>{a.title}</span>
          {count > 0 && !big && <span className="ml-auto shrink-0 rounded-full px-1.5 text-[10px] font-semibold" style={{ background: "rgba(255,255,255,.08)" }}>{count}</span>}
        </div>
        {head ? <div className="mt-1 truncate text-[11px] opacity-60">{OWNER[a.team]} · {a.job}</div>
          : <div className={`mt-0.5 ${big ? "line-clamp-2" : "truncate"} text-[11px] opacity-60`}>{a.kind === "client" ? (st === "waiting" ? "An approval is waiting for you" : "Approves every stage") : line}</div>}
      </div>
    </div>
  );
}

function Profile({ a, msgs, v, st, org, onPick, onClose }: { a: Agent; msgs: BusMsg[]; v: Json; st: string; org: Agent[]; onPick: (id: string) => void; onClose: () => void }) {
  const logs = msgs.filter((m) => m.type === "LOG" || m.recipient === "theater");
  const talk = msgs.filter(talkOf);
  const secs = msgs.length > 1 ? Math.round((new Date(msgs[msgs.length - 1].created_at).getTime() - new Date(msgs[0].created_at).getTime()) / 1000) : 0;
  const boss = a.parent ? org.find((o) => o.id === a.parent) : a.kind === "client" ? org.find((o) => o.id === "rune") : null;
  const team = org.filter((o) => o.parent === a.id);
  const peers = Array.from(new Set(talk.flatMap((m) => [m.sender, m.recipient])))
    .filter((p) => p !== a.id && !team.some((t) => t.id === p)).map((p) => org.find((o) => o.id === p)).filter(Boolean) as Agent[];
  const result = a.id === "rune" ? `${STAGES.length} stages · ${v.brief?.business_name ?? "no campaign yet"}` : outcome(a.stage, v);
  const highlights = logs.slice(-3).map(msgText);
  const color = TEAM_COLOR[a.team];
  const chip = (o: Agent) => (
    <button key={o.id} onClick={() => onPick(o.id)} className="inline-flex items-center gap-1.5 rounded-full py-1 pl-1 pr-2.5 text-[11.5px] font-semibold hover:brightness-125" style={{ background: "var(--night-3)", color: "var(--night-text)" }}>
      <span className="grid h-5 w-5 place-items-center rounded-full text-[9px] font-bold" style={{ background: TEAM_COLOR[o.team], color: "#141a0a" }}><Glyph a={o} size={11} /></span>{o.title}
    </button>
  );
  return (
    <aside className="absolute bottom-3 right-3 top-3 flex w-[min(460px,calc(100%-24px))] flex-col rounded-[22px] fade-in" onPointerDown={(e) => e.stopPropagation()}
      style={{ background: "rgba(15,18,23,.96)", border: "1px solid rgba(255,255,255,.08)", backdropFilter: "blur(16px)", boxShadow: "0 24px 60px rgba(0,0,0,.45)", cursor: "default" }}>
      <div className="flex items-start gap-3 p-5 pb-3">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl text-[14px] font-bold" style={{ background: a.kind === "client" ? "#fff" : color, color: "#141a0a" }}><Glyph a={a} size={22} /></span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px]" style={{ color: "var(--night-text-2)" }}>{a.kind === "dept" ? "Department" : a.kind === "client" ? "Client" : a.id}{OWNER[a.team] ? ` · ${OWNER[a.team]}` : ""}</div>
          <div className="text-[19px] font-semibold leading-tight">{a.title}</div>
          <span className="mt-1.5 inline-flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: STATUS[st][0] }}>
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: "currentColor" }} />{STATUS[st][1]}
          </span>
        </div>
        <button onClick={onClose} className="grid h-8 w-8 place-items-center rounded-full" style={{ background: "var(--night-3)" }} aria-label="Close"><X size={14} /></button>
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto px-5 pb-5">
        <p className="text-[12.5px] leading-relaxed" style={{ color: "var(--night-text-2)" }}>{a.job}</p>

        {(result || highlights.length > 0) && (
          <div className="mt-4 rounded-2xl p-4" style={{ background: "var(--night-2)" }}>
            <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--night-text-2)" }}>What it delivered</div>
            {result && <div className="mt-1.5 text-[14px] font-semibold" style={{ color }}>{result}</div>}
            {highlights.map((h, i) => <div key={i} className="mt-1.5 line-clamp-2 text-[12px] leading-snug opacity-85">· {h}</div>)}
          </div>
        )}

        <div className="mt-3 grid grid-cols-3 gap-2">
          {([[Timer, dur(secs), "busy for"], [Layers, logs.length, "steps"], [MessagesSquare, talk.length, "messages"]] as const).map(([Ic, n, l]) => (
            <div key={l} className="rounded-2xl p-3" style={{ background: "var(--night-2)" }}>
              <Ic size={12} style={{ color: "var(--night-text-2)" }} />
              <div className="mt-1 text-[17px] font-semibold tabular-nums">{n}</div>
              <div className="text-[11px]" style={{ color: "var(--night-text-2)" }}>{l}</div>
            </div>
          ))}
        </div>

        {(boss || team.length > 0 || peers.length > 0) && (
          <div className="mt-4 flex flex-col gap-2.5 text-[11px]" style={{ color: "var(--night-text-2)" }}>
            {boss && <div className="flex flex-wrap items-center gap-1.5"><span className="w-20">Reports to</span>{chip(boss)}</div>}
            {team.length > 0 && <div className="flex flex-wrap items-center gap-1.5"><span className="w-20">Manages</span>{team.map(chip)}</div>}
            {peers.length > 0 && <div className="flex flex-wrap items-center gap-1.5"><span className="w-20">Talked with</span>{peers.map(chip)}</div>}
          </div>
        )}

        <div className="mt-5 text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--night-text-2)" }}>Work log</div>
        <div className="mt-1">
          {msgs.length ? <Feed msgs={msgs} working={st === "working"} keepOpen={4} /> : <p className="py-6 text-center text-[12px]" style={{ color: "var(--night-text-2)" }}>Not needed on this campaign yet.</p>}
        </div>
      </div>
    </aside>
  );
}
