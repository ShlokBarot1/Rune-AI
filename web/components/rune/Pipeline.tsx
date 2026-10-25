"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { Check, Loader2, Hand } from "lucide-react";
import { msgText, useEvents, type BusMsg } from "@/lib/api";
import { STAGES, agentColor, agentName, stageLabel, type StageId, type StageState } from "@/lib/run";

export function PipelineRail({ states, selected, onSelect }: {
  states: Record<StageId, StageState>; selected: StageId; onSelect: (s: StageId) => void;
}) {
  return (
    <div className="rail py-1">
      {STAGES.map((s, i) => {
        const st = states[s.id];
        return (
          <Fragment key={s.id}>
            {i > 0 && <span className="link" />}
            <button
              className={`stage ${st} ${selected === s.id ? "selected" : ""} ${st === "running" ? "live-pulse" : ""}`}
              onClick={() => onSelect(s.id)}
              title={`${s.label} · ${s.owner}`}
            >
              <span className="dot">
                {st === "done" ? <Check size={12} strokeWidth={3} /> : st === "running" ? <Loader2 size={12} className="animate-spin" /> :
                  st === "waiting" ? <Hand size={11} /> : <span className="text-[10px]">{i + 1}</span>}
              </span>
              {s.label}
            </button>
          </Fragment>
        );
      })}
    </div>
  );
}

const VERBS = ["Thinking", "Reasoning", "Exploring", "Weighing options", "Drafting", "Checking"];
const secs = (a: string, b: string) => Math.max(1, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));

/** One agent step, readable: "a | b | c" lists become bullets, long text folds. */
function Step({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const parts = text.includes(" | ") ? text.split(" | ") : null;
  if (parts) {
    const [head, first] = parts[0].includes(": ") ? [parts[0].slice(0, parts[0].indexOf(": ") + 1), parts[0].slice(parts[0].indexOf(": ") + 2)] : ["", parts[0]];
    const items = [first, ...parts.slice(1)];
    return (
      <div className="flex flex-col gap-1">
        {head && <span>{head}</span>}
        {(open ? items : items.slice(0, 3)).map((x, i) => <span key={i} className="pl-3 opacity-85" style={{ borderLeft: "2px solid var(--night-3)" }}>{x}</span>)}
        {items.length > 3 && <button className="self-start text-[11px] underline opacity-60" onClick={() => setOpen(!open)}>{open ? "less" : `+${items.length - 3} more`}</button>}
      </div>
    );
  }
  if (text.length <= 170) return <span>{text}</span>;
  return (
    <span>{open ? text : `${text.slice(0, 160)}…`} <button className="text-[11px] underline opacity-60" onClick={() => setOpen(!open)}>{open ? "less" : "more"}</button></span>
  );
}

type Block = { kind: "thought"; stage: string; sender: string; msgs: BusMsg[] } | { kind: "msg"; m: BusMsg };

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** A stage's steps as lines: web searches merge into one list, and when several agents share a stage
 *  (the 3 researchers) each line says who did it. */
function stepLines(msgs: BusMsg[]) {
  const multi = new Set(msgs.map((m) => m.sender)).size > 1;
  const out: { seq: number; text: string; search?: boolean }[] = [];
  for (const m of msgs) {
    const t = msgText(m);
    const q = t.startsWith("Searched: ") ? t.slice(10) : null;
    const prev = out[out.length - 1];
    if (q && prev?.search) { prev.text += ` | ${q}`; continue; }
    const parts = m.sender.split(".");
    const who = multi && parts.length > 2 ? `${cap(parts[parts.length - 1])}: ` : "";
    out.push(q ? { seq: m.seq, text: `Searched the web: ${q}`, search: true } : { seq: m.seq, text: who + t });
  }
  return out;
}

function Thought({ b, end, live, collapsed }: { b: Extract<Block, { kind: "thought" }>; end: string; live: boolean; collapsed: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  useEffect(() => { setOpen(!collapsed); }, [collapsed]);
  const [bg, fg] = agentColor(b.sender);
  const verb = VERBS[b.msgs[0].seq % VERBS.length];
  const lines = stepLines(b.msgs);
  const result = lines[lines.length - 1].text;
  return (
    <div className="fade-in py-2">
      <button className="flex w-full items-center gap-2.5 text-left" onClick={() => setOpen(!open)}>
        <span className="agent-dot" style={{ background: bg, color: fg }}>{b.stage.slice(0, 2).toUpperCase()}</span>
        <span className="font-semibold" style={{ color: bg === "#ffffff" ? "#fff" : bg }}>{b.stage}</span>
        <span className={`text-[11.5px] ${live ? "shimmer" : ""}`} style={{ color: "var(--night-text-2)" }}>
          {live ? `${verb}…` : `took ${secs(b.msgs[0].created_at, end)}s`}
        </span>
        <span className="ml-auto text-[11px]" style={{ color: "var(--night-text-2)" }}>{open ? "▾" : `▸ ${lines.length} step${lines.length > 1 ? "s" : ""}`}</span>
      </button>
      {!open && (  // folded: just the outcome, one line
        <div className="ml-[35px] mt-1 truncate text-[12px]" style={{ color: "var(--night-text)" }} title={result}>{result.split(" | ")[0]}</div>
      )}
      {open && (
        <div className="ml-[13px] mt-1.5 flex flex-col gap-1.5 border-l pl-[22px] text-[12.5px] leading-relaxed" style={{ borderColor: "var(--night-3)", color: "var(--night-text)" }}>
          {lines.map((l) => <Step key={l.seq} text={l.text} />)}
        </div>
      )}
    </div>
  );
}

const between = (m: BusMsg) =>
  (m.type === "REQUEST" || m.type === "RESPONSE") && !["human", "theater"].includes(m.recipient) && m.sender !== "human";

/** Agents are "working" while the bus has moved in the last 20 s. */
export const isWorking = (msgs: BusMsg[]) => {
  const last = msgs[msgs.length - 1];
  return !!last && Date.now() - new Date(last.created_at).getTime() < 20000;
};

/** The dark live panel: every agent step, with agent-to-agent conversations shown as chat bubbles. */
export function Theater({ clientId, height = 640, compact = false }: { clientId: string | null; height?: number | string; compact?: boolean }) {
  const msgs = useEvents(clientId, 400);
  const box = useRef<HTMLDivElement>(null);  // scroll only the feed, never the page
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [msgs.length]);
  const shown = msgs.filter((m) => m.type !== "LOG" || !String(m.payload?.text ?? "").startsWith("Searched:") || !compact);
  const working = isWorking(shown);

  return (
    <section className="panel-dark flex flex-col" style={{ height }}>
      <div className="flex items-center justify-between pb-3">
        <div>
          <div className="label">Agent Theater</div>
          <div className="text-[17px] font-semibold tracking-tight">Live orchestration</div>
        </div>
        <span className={`pill ${working ? "lime" : "dark"}`}>{working ? "● agents working" : "idle"}</span>
      </div>
      <div ref={box} className="scroll-thin -mr-2 flex-1 overflow-y-auto pr-2">
        {shown.length === 0 && <p className="meta py-8 text-center">Waiting for the first agent…</p>}
        <Feed msgs={shown} working={working} />
      </div>
    </section>
  );
}

/** Steps grouped into "thoughts" per agent; conversations, questions and your decisions stand alone. */
export function Feed({ msgs: shown, working, keepOpen = 6 }: { msgs: BusMsg[]; working: boolean; keepOpen?: number }) {
  // consecutive steps of one agent = one "thought"; conversations, questions and your decisions stand alone
  // consecutive steps of one stage = one block, whichever of its agents posted them
  const blocks: Block[] = [];
  let last: BusMsg | undefined;
  for (const m of shown) {
    if (last && last.sender === m.sender && last.type === m.type && msgText(last) === msgText(m)) continue;  // repeats
    last = m;
    const step = m.sender !== "human" && !between(m) && m.type !== "QUESTION" && (m.type === "LOG" || m.recipient === "theater");
    const prev = blocks[blocks.length - 1];
    const stage = stageLabel(m.sender);
    if (step && prev?.kind === "thought" && prev.stage === stage) prev.msgs.push(m);
    else blocks.push(step ? { kind: "thought", stage, sender: m.sender, msgs: [m] } : { kind: "msg", m });
  }

  return (
    <>
        {blocks.map((b, i) => {
          if (b.kind === "thought") {
            const next = blocks[i + 1];
            const end = next ? (next.kind === "thought" ? next.msgs[0] : next.m).created_at : new Date().toISOString();
            return <Thought key={b.msgs[0].seq} b={b} end={end} live={!next && !!working} collapsed={i < blocks.length - keepOpen} />;
          }
          const m = b.m;
          if (m.sender === "human") {  // your decisions: quiet one-liners
            const what = m.payload?.answer ?? (m.payload?.action === "approve" ? "approved" : m.payload?.action === "reject" ? "asked to redo" :
              m.payload?.action === "changes" ? `requested: “${m.payload.comment}”` : m.payload?.action);
            return (
              <div key={m.seq} className="fade-in flex items-center gap-2 py-1.5 text-[11.5px]" style={{ color: "var(--night-text-2)" }}>
                <span className="h-1.5 w-1.5 rounded-full bg-white/60" /> You {m.payload?.answer != null ? `answered “${what}”` : what}
                <span className="opacity-60">· {String(m.recipient).replace("malav.", "")}</span>
              </div>
            );
          }
          if (between(m)) {
            const right = m.sender.startsWith("malav");
            return (
              <div key={m.seq} className={`fade-in my-2 flex flex-col ${right ? "items-end" : "items-start"}`}>
                <div className="meta mb-1">{agentName(m.sender)} → {agentName(m.recipient)}</div>
                <div className={`bubble ${right ? "right" : "left"}`}>{msgText(m)}</div>
              </div>
            );
          }
          if (m.type === "QUESTION" || (m.recipient === "human" && m.type !== "LOG")) {
            return (
              <div key={m.seq} className="fade-in my-2 rounded-2xl p-3 text-[12.5px]" style={{ background: "rgba(255,207,122,.12)", color: "#ffcf7a" }}>
                <b>{agentName(m.sender)}</b> · {msgText(m)}
              </div>
            );
          }
          return null;
        })}
    </>
  );
}
