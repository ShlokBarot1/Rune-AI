"use client";
import { useState } from "react";
import { Check, Hand, MessageSquare, Rocket, RotateCcw, Send } from "lucide-react";
import Artifact, { AuditView } from "@/components/Artifacts";
import Avatar from "@/components/Avatar";
import { GATE_TITLES, post, type Json } from "@/lib/api";

const OWNER: Record<string, string> = {
  intake: "malav.intake", research: "malav.research", strategy: "malav.strategy", hooks: "malav.hooks",
  creatives: "malav.creatives", budget: "shlok.budget", targeting: "shlok.targeting", go_live: "auditor",
};

/** One decision the pipeline is waiting on — approval-dock styling, with the artifact under review. */
export default function DecisionCard({ item, onDone }: { item: Json; onDone: () => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const send = async (body: Json) => {
    setBusy(true);
    try { await post(`/runs/${item.client_id}/resume`, body); setComment(""); setOpen(false); }
    finally { setBusy(false); onDone(); }
  };

  const gate = item.gate ?? item.type;
  const title =
    item.type === "question" ? item.question :
    item.type === "pushback" ? `The agents pushed back on your change to ${GATE_TITLES[item.gate] ?? item.gate}` :
    item.type === "go_live" ? "Audit done — ready to go live?" :
    `Review: ${GATE_TITLES[gate] ?? gate}`;

  return (
    <section className="card fade-in flex flex-col gap-4" style={{ background: "rgba(255,255,255,.92)" }}>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full" style={{ background: "var(--accent)", color: "var(--accent-ink)" }}><Hand size={16} /></span>
        <div className="min-w-0 flex-1">
          <div className="label">{item.client} · {item.type === "question" ? `${item.agent} asks you` : "waiting for you"}</div>
          <div className="text-[17px] font-semibold leading-snug">{title}</div>
        </div>
        <div className="flex items-center gap-2">
          <Avatar name={item.agent ?? OWNER[gate] ?? "core"} />
          <span className="pill dark hidden sm:inline-flex">{item.type === "gate" ? "approval gate" : item.type.replace("_", " ")}</span>
        </div>
      </div>

      {item.type === "question" && (
        <div className="flex flex-col gap-2 pl-12">
          <div className="flex flex-wrap gap-2">
            {item.options?.map((o: string, i: number) => (
              <button key={o} disabled={busy} className={`btn ${i === 0 ? "btn-lime" : "btn-ghost"} !h-9`} onClick={() => send({ answer: o })}>{o}</button>
            ))}
          </div>
          <div className="flex gap-2">
            <input className="input" placeholder="Or type your own answer…" value={comment} onChange={(e) => setComment(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && comment && send({ answer: comment })} />
            <button disabled={busy || !comment} className="btn btn-primary" onClick={() => send({ answer: comment })} aria-label="Send"><Send size={14} /></button>
          </div>
        </div>
      )}

      {item.type === "pushback" && (
        <div className="flex flex-col gap-3 pl-12">
          <div className="bubble light-left !max-w-full text-[13.5px]">{item.reply}</div>
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} className="btn btn-primary" onClick={() => send({ action: "reject" })}>Keep current output</button>
            <button disabled={busy} className="btn btn-ghost" onClick={() => send({ action: "approve" })}>Apply my change anyway</button>
          </div>
        </div>
      )}

      {(item.type === "gate" || item.type === "go_live") && (
        <>
          <div className="sm:pl-12">{item.type === "go_live" ? (item.audit ? <AuditView v={item.audit} /> : null) : <Artifact artifact={item.artifact} />}</div>
          <div className="flex flex-col gap-2 sm:pl-12">
            {open && (
              <textarea className="input min-h-20" autoFocus value={comment} onChange={(e) => setComment(e.target.value)}
                placeholder="What should change? e.g. “put more budget on the fitness ad set” — checked against the brief, policy and budget first." />
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
        </>
      )}
    </section>
  );
}
