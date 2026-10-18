"use client";
import { useState } from "react";
import { Check, Hand, MessageSquare, Rocket, RotateCcw, Send } from "lucide-react";
import { GATE_TITLES, post, type Json } from "@/lib/api";

/** What the run is waiting on (gate / question / pushback / go-live), docked at the bottom of the canvas. */
export default function ApprovalDock({ item, clientId, onDone, prefill }: {
  item: Json; clientId: string; onDone: () => void; prefill?: string;
}) {
  const [comment, setComment] = useState(prefill ?? "");
  const [open, setOpen] = useState(!!prefill);
  const [busy, setBusy] = useState(false);
  const send = async (body: Json) => {
    setBusy(true);
    try { await post(`/runs/${clientId}/resume`, body); setComment(""); setOpen(false); }
    finally { setBusy(false); onDone(); }
  };

  const title = item.type === "question" ? item.question
    : item.type === "pushback" ? "The agents pushed back on your change"
    : item.type === "go_live" ? "Audit done — ready to go live?"
    : `Review: ${GATE_TITLES[item.gate] ?? item.gate}`;

  return (
    <section className="card fade-in !p-4 sm:!p-5" style={{ background: "rgba(255,255,255,.92)" }}>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: "var(--accent)", color: "var(--accent-ink)" }}><Hand size={16} /></span>
        <div className="min-w-0 flex-1">
          <div className="label">{item.type === "question" ? `${item.agent} asks you` : "Waiting for you"}</div>
          <div className="text-[16px] font-semibold leading-snug">{title}</div>
          {item.type === "pushback" && <p className="tile mt-2 text-[13px] leading-relaxed">{item.reply}</p>}
        </div>
      </div>

      {item.type === "question" && (
        <div className="mt-3 flex flex-col gap-2 pl-12">
          <div className="flex flex-wrap gap-2">
            {item.options?.map((o: string, i: number) => (
              <button key={o} disabled={busy} className={`btn ${i === 0 ? "btn-lime" : "btn-ghost"} !h-9`} onClick={() => send({ answer: o })}>{o}</button>
            ))}
          </div>
          <div className="flex gap-2">
            <input className="input" placeholder="Or type your own answer…" value={comment} onChange={(e) => setComment(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && comment && send({ answer: comment })} />
            <button disabled={busy || !comment} className="btn btn-primary" onClick={() => send({ answer: comment })}><Send size={14} /></button>
          </div>
        </div>
      )}

      {item.type === "pushback" && (
        <div className="mt-3 flex flex-wrap gap-2 pl-12">
          <button disabled={busy} className="btn btn-primary" onClick={() => send({ action: "reject" })}>Keep current output</button>
          <button disabled={busy} className="btn btn-ghost" onClick={() => send({ action: "approve" })}>Apply my change anyway</button>
        </div>
      )}

      {(item.type === "gate" || item.type === "go_live") && (
        <div className="mt-3 flex flex-col gap-2 pl-12">
          {open && (
            <textarea className="input min-h-20" autoFocus value={comment} onChange={(e) => setComment(e.target.value)}
              placeholder="What should change? The suggestion evaluator checks it against the brief, policy and budget." />
          )}
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} className="btn btn-lime" onClick={() => send({ action: "approve" })}>
              {item.type === "go_live" ? <><Rocket size={15} /> Go live</> : <><Check size={15} /> Approve</>}
            </button>
            {open
              ? <button disabled={busy || !comment} className="btn btn-primary" onClick={() => send({ action: "changes", comment })}><Send size={14} /> Send change request</button>
              : <button disabled={busy} className="btn btn-ghost" onClick={() => setOpen(true)}><MessageSquare size={15} /> Request changes</button>}
            <button disabled={busy} className="btn btn-danger" onClick={() => send({ action: "reject", comment })}>
              <RotateCcw size={15} /> {item.type === "go_live" ? "Don't launch" : "Redo"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
