"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { PipelineRail } from "@/components/rune/Pipeline";
import { ClientPicker, useClient } from "@/components/useClient";
import { msgText, useEvents, usePoll, type BusMsg, type Json } from "@/lib/api";
import { agentColor, agentName, agentShort, currentStage, stageStates, type StageId } from "@/lib/run";

const TEAMS = ["all", "malav", "shlok", "auditor", "human"] as const;
const READ_TOOLS = new Set(["get_ads", "get_trend"]);

const isTalk = (m: BusMsg) =>
  (m.type === "REQUEST" || m.type === "RESPONSE") && !["human", "theater"].includes(m.recipient) && m.sender !== "human"
  && m.sender !== m.recipient;

export default function TheaterPage() {
  const { runs, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 2000);
  const msgs = useEvents(clientId, 600);
  const [team, setTeam] = useState<(typeof TEAMS)[number]>("all");
  const [stage, setStage] = useState<StageId | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const shown = msgs.filter((m) => team === "all" || m.sender.startsWith(team) || m.recipient.startsWith(team));
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [shown.length]);
  const last = msgs[msgs.length - 1];
  const working = last && Date.now() - new Date(last.created_at).getTime() < 20000;

  const roster = useMemo(() => {
    const n: Record<string, number> = {};
    for (const m of msgs) if (m.sender !== "theater") n[m.sender] = (n[m.sender] ?? 0) + 1;
    return Object.entries(n).sort((a, b) => b[1] - a[1]);
  }, [msgs]);
  const talks = msgs.filter(isTalk).length;
  const steps = msgs.filter((m) => m.payload?.tool).length;
  const rejected = msgs.filter((m) => String(m.payload?.observation ?? "").startsWith("REJECTED")).length;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Launch &amp; optimise</div>
          <h1 className="page-title mt-1">Agent Theater</h1>
        </div>
        <div className="flex items-center gap-2">
          <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
          <span className={`pill ${working ? "lime" : ""} !px-3 !py-1.5 !text-[12px]`}>{working ? "● agents working" : "idle"}</span>
        </div>
      </div>

      <div className="card !p-3">
        <PipelineRail states={stageStates(run)} selected={stage ?? currentStage(run)} onSelect={setStage} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <section className="panel-dark flex flex-col" style={{ height: "calc(100vh - 290px)", minHeight: 520 }}>
          <div className="flex flex-wrap items-center justify-between gap-3 pb-3">
            <div>
              <div className="label">Every step, tool call and agent-to-agent message</div>
              <div className="text-[17px] font-semibold tracking-tight">Live orchestration</div>
            </div>
            <div className="seg !bg-[var(--night-2)] !border-transparent">
              {TEAMS.map((t) => (
                <button key={t} onClick={() => setTeam(t)} className={team === t ? "on !bg-[var(--accent)] !text-[var(--accent-ink)]" : "!text-[var(--night-text-2)]"}>{t}</button>
              ))}
            </div>
          </div>
          <div ref={box} className="scroll-thin -mr-2 flex-1 overflow-y-auto pr-2">
            {shown.length === 0 && <p className="meta py-8 text-center">Waiting for the first agent…</p>}
            {shown.map((m) => <Line key={m.seq} m={m} />)}
            {working && <div className="typing py-2 pl-9" style={{ color: "var(--accent)" }}><span /><span /><span /></div>}
          </div>
        </section>

        <div className="flex flex-col gap-4">
          <section className="card !p-5" style={{ background: "var(--ink)", color: "#fff", borderColor: "transparent" }}>
            <div className="label" style={{ color: "#9aa3b2" }}>This campaign</div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center">
              {[["messages", msgs.length], ["agent talks", talks], ["tool calls", steps]].map(([k, n]) => (
                <div key={k as string}><div className="card-num !text-[22px]">{n}</div><div className="meta" style={{ color: "#9aa3b2" }}>{k}</div></div>
              ))}
            </div>
            {rejected > 0 && <div className="mt-3 rounded-2xl px-3 py-2 text-[12px]" style={{ background: "rgba(208,73,62,.18)", color: "#ff9b91" }}>{rejected} unsafe action(s) blocked by guardrails</div>}
          </section>
          <section className="card !p-5">
            <div className="label">Agents</div>
            <div className="mt-2 flex flex-col">
              {roster.length === 0 && <div className="meta py-3">None active yet.</div>}
              {roster.map(([name, n]) => {
                const [bg, fg] = agentColor(name);
                return (
                  <button key={name} onClick={() => setTeam(name.split(".")[0] as (typeof TEAMS)[number])} className="row text-left">
                    <span className="agent-dot" style={{ background: bg, color: fg, border: bg === "#ffffff" ? "1px solid var(--line)" : undefined }}>{agentShort(name)}</span>
                    <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">{name}</span>
                    <span className="pill">{n}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function Line({ m }: { m: BusMsg }) {
  const [open, setOpen] = useState(false);
  const [bg, fg] = agentColor(m.sender);
  const p = m.payload ?? {};

  if (m.sender === "human") {
    const what = p.answer != null ? `answered “${p.answer}”` : p.action === "approve" ? "approved" : p.action === "reject" ? "asked to redo"
      : p.action === "changes" ? `requested: “${p.comment}”` : p.approved ? "approved an optimizer action" : p.action;
    return (
      <div className="fade-in flex items-center gap-2 py-1.5 text-[11.5px]" style={{ color: "var(--night-text-2)" }}>
        <span className="h-1.5 w-1.5 rounded-full bg-white/60" /> You {what}<span className="opacity-60">· {agentName(m.recipient)}</span>
      </div>
    );
  }
  if (isTalk(m)) {
    const right = m.sender.startsWith("malav");
    return (
      <div className={`fade-in my-2 flex flex-col ${right ? "items-end" : "items-start"}`}>
        <div className="meta mb-1">{agentName(m.sender)} → {agentName(m.recipient)}</div>
        <div className={`bubble ${right ? "right" : "left"}`}>{msgText(m)}</div>
      </div>
    );
  }
  if (m.type === "QUESTION" || (m.recipient === "human" && m.type !== "LOG")) {
    return (
      <div className="fade-in my-2 rounded-2xl p-3 text-[12.5px]" style={{ background: "rgba(255,207,122,.12)", color: "#ffcf7a" }}>
        <b>{agentName(m.sender)}</b> · {msgText(m)}
      </div>
    );
  }
  const rejected = String(p.observation ?? "").startsWith("REJECTED");
  return (
    <div className="feed-line fade-in">
      <span className="agent-dot" style={{ background: bg, color: fg }}>{agentShort(m.sender)}</span>
      <div className="min-w-0 flex-1">
        <button onClick={() => setOpen(!open)} className="block w-full text-left">
          <span className="font-semibold" style={{ color: bg === "#ffffff" ? "#fff" : bg }}>{agentName(m.sender)}</span>{" "}
          {p.step && <span className="meta mr-1">step {p.step}</span>}
          <span style={{ color: "var(--night-text)" }}>{msgText(m)}</span>
        </button>
        {p.tool && (
          <div className="mt-1.5 flex flex-wrap items-start gap-1.5">
            <span className={`pill ${READ_TOOLS.has(p.tool) ? "dark" : "lime"}`}>{p.tool}</span>
            <span className="text-[11.5px] leading-snug"
              style={rejected ? { color: "#ff9b91" } : { color: "var(--night-text-2)" }}>
              {String(p.observation ?? "").slice(0, 180)}
            </span>
          </div>
        )}
        {open && <pre className="mt-2 max-h-60 overflow-auto rounded-xl p-3 text-[11px]" style={{ background: "var(--night-2)", color: "var(--night-text-2)" }}>{JSON.stringify(p, null, 2)}</pre>}
      </div>
    </div>
  );
}
