"use client";
import { Fragment, useEffect, useRef } from "react";
import { Check, Loader2, Hand } from "lucide-react";
import { msgText, useEvents, type BusMsg } from "@/lib/api";
import { STAGES, agentColor, agentName, agentShort, type StageId, type StageState } from "@/lib/run";

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

const between = (m: BusMsg) =>
  (m.type === "REQUEST" || m.type === "RESPONSE") && !["human", "theater"].includes(m.recipient) && m.sender !== "human";

/** The dark live panel: every agent step, with agent-to-agent conversations shown as chat bubbles. */
export function Theater({ clientId, height = 640, compact = false }: { clientId: string | null; height?: number; compact?: boolean }) {
  const msgs = useEvents(clientId, 400);
  const box = useRef<HTMLDivElement>(null);  // scroll only the feed, never the page
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [msgs.length]);
  const shown = msgs.filter((m) => m.type !== "LOG" || !String(m.payload?.text ?? "").startsWith("Searched:") || !compact);
  const last = shown[shown.length - 1];
  const working = last && Date.now() - new Date(last.created_at).getTime() < 20000;

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
        {shown.map((m) => {
          const [bg, fg] = agentColor(m.sender);
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
          return (
            <div key={m.seq} className="feed-line fade-in">
              <span className="agent-dot" style={{ background: bg, color: fg }}>{agentShort(m.sender)}</span>
              <div className="min-w-0">
                <span className="font-semibold" style={{ color: bg === "#ffffff" ? "#fff" : bg }}>{agentName(m.sender)}</span>{" "}
                <span style={{ color: "var(--night-text)" }}>{msgText(m)}</span>
              </div>
            </div>
          );
        })}
        {working && <div className="typing py-2 pl-9" style={{ color: "var(--accent)" }}><span /><span /><span /></div>}
      </div>
    </section>
  );
}
