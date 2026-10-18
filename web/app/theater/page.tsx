"use client";
import { useState } from "react";
import Avatar from "@/components/Avatar";
import { ClientPicker, useClient } from "@/components/useClient";
import { msgText, useEvents, usePoll, type BusMsg, type Json } from "@/lib/api";

const STAGES: { node: string; key: string; label: string; team: string }[] = [
  { node: "intake", key: "brief", label: "Intake", team: "malav" },
  { node: "research", key: "research", label: "Research", team: "malav" },
  { node: "strategy", key: "blueprint", label: "Strategy", team: "malav" },
  { node: "hooks", key: "hooks", label: "Hooks", team: "malav" },
  { node: "creatives", key: "package", label: "Creatives", team: "malav" },
  { node: "budget", key: "budget_plan", label: "Budget", team: "shlok" },
  { node: "targeting", key: "targeting", label: "Targeting", team: "shlok" },
  { node: "launch", key: "launch", label: "Meta build", team: "shlok" },
  { node: "audit", key: "audit", label: "Audit", team: "auditor" },
  { node: "go_live", key: "live", label: "Go live", team: "human" },
];

const TYPE_TONE: Record<string, string> = { QUESTION: "warn", FIX_PROMPT: "bad", REQUEST: "blue", RESPONSE: "ok", FEEDBACK: "blue" };

export default function Theater() {
  const { runs, clientId, setClientId } = useClient();
  const [run] = usePoll<Json>(clientId ? `/runs/${clientId}` : null, 2000);
  const msgs = useEvents(clientId, 400);
  const [team, setTeam] = useState<string>("all");
  const v = run?.values ?? {};
  const next: string[] = run?.next ?? [];

  const shown = [...msgs].reverse().filter((m) => team === "all" || m.sender.startsWith(team) || m.recipient.startsWith(team));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[28px] tracking-tight" style={{ fontWeight: 500 }}>Agent Theater</h1>
          <p className="label mt-1">Every agent step, tool call and message between agents, live.</p>
        </div>
        <ClientPicker runs={runs} value={clientId} onChange={setClientId} />
      </div>

      <section className="card overflow-x-auto">
        <ol className="flex min-w-[760px] items-center">
          {STAGES.map((s, i) => {
            const done = v[s.key] != null && !(s.key === "live" && !v.live);
            const waiting = next.includes(`${s.node}__gate`) || (s.node === "go_live" && next.includes("go_live"));
            const active = next.includes(s.node) || (run?.running && !done && STAGES.slice(0, i).every((p) => v[p.key] != null));
            return (
              <li key={s.node} className="flex flex-1 items-center">
                <div className="flex flex-col items-center gap-1.5">
                  <span
                    className={`grid h-9 w-9 place-items-center rounded-full text-[11px] font-semibold transition-colors ${active ? "animate-pulse" : ""}`}
                    style={{
                      background: done ? "var(--primary)" : waiting ? "var(--warn-bg)" : active ? "color-mix(in srgb, var(--primary) 25%, transparent)" : "var(--tile)",
                      color: done ? "#fff" : waiting ? "var(--warn)" : "var(--text-2)",
                    }}
                  >
                    {i + 1}
                  </span>
                  <span className="text-[11.5px] font-medium">{s.label}</span>
                  <span className="meta">{waiting ? "needs you" : done ? "done" : active ? "working" : s.team}</span>
                </div>
                {i < STAGES.length - 1 && <span className="mx-1 mb-8 h-px flex-1" style={{ background: done ? "var(--primary)" : "var(--line)" }} />}
              </li>
            );
          })}
        </ol>
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="title">Agent bus</span>
          <div className="flex gap-1.5">
            {["all", "malav", "shlok", "auditor", "human"].map((t) => (
              <button key={t} onClick={() => setTeam(t)} className={`pill ${team === t ? "blue" : ""}`}>{t}</button>
            ))}
          </div>
        </div>
        <div className="mt-2 max-h-[62vh] overflow-y-auto pr-1">
          {shown.length === 0 && <div className="meta py-6">No messages yet.</div>}
          {shown.map((m) => <Line key={m.seq} m={m} />)}
        </div>
      </section>
    </div>
  );
}

function Line({ m }: { m: BusMsg }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="row fade-in !items-start">
      <Avatar name={m.sender} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13px] font-medium">{m.sender}</span>
          {m.recipient !== "theater" && <span className="meta">→ {m.recipient}</span>}
          {m.type !== "LOG" && <span className={`pill ${TYPE_TONE[m.type] ?? ""}`}>{m.type.replace("_", " ").toLowerCase()}</span>}
          <span className="meta ml-auto">{new Date(m.created_at).toLocaleTimeString()}</span>
        </div>
        <button onClick={() => setOpen(!open)} className="mt-0.5 block w-full text-left text-[13px] leading-relaxed" style={{ color: "var(--text-2)" }}>
          {m.payload?.step ? <span className="meta mr-1.5">step {m.payload.step}</span> : null}
          {msgText(m)}
        </button>
        {m.payload?.tool && (
          <div className="mt-1.5 flex flex-wrap items-start gap-1.5 text-[12px]">
            <span className="pill blue">{m.payload.tool}</span>
            <span className={m.payload.observation?.startsWith("REJECTED") ? "pill bad" : "meta"} style={{ whiteSpace: "normal" }}>
              {String(m.payload.observation ?? "").slice(0, 160)}
            </span>
          </div>
        )}
        {open && <pre className="tile mt-2 max-h-60 overflow-auto text-[11px]">{JSON.stringify(m.payload, null, 2)}</pre>}
      </div>
    </div>
  );
}
