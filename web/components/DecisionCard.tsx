"use client";
import { useState } from "react";
import { Check, MessageSquare, RotateCcw, Rocket } from "lucide-react";
import Artifact, { AuditView } from "@/components/Artifacts";
import { GATE_TITLES, post, type Json } from "@/lib/api";

export default function DecisionCard({ item, onDone }: { item: Json; onDone: () => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const send = async (body: Json) => {
    setBusy(true);
    try {
      await post(`/runs/${item.client_id}/resume`, body);
      setComment("");
      setOpen(false);
    } finally {
      setBusy(false);
      onDone();
    }
  };

  const heading =
    item.type === "question" ? `${item.agent} asks` :
    item.type === "pushback" ? `Agent reply on ${GATE_TITLES[item.gate] ?? item.gate}` :
    GATE_TITLES[item.gate ?? item.type] ?? item.gate ?? item.type;

  return (
    <section className="card fade-in flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="label">{item.client}</div>
          <h2 className="text-[17px]" style={{ fontWeight: 500 }}>{heading}</h2>
        </div>
        <span className={`pill ${item.type === "go_live" ? "ok" : "blue"}`}>{item.type === "gate" ? "approval gate" : item.type.replace("_", " ")}</span>
      </div>

      {item.type === "question" && (
        <>
          <p className="text-[15px]">{item.question}</p>
          <div className="flex flex-wrap gap-2">
            {item.options?.map((o: string) => (
              <button key={o} disabled={busy} className="btn btn-ghost" onClick={() => send({ answer: o })}>{o}</button>
            ))}
          </div>
          <div className="flex gap-2">
            <input className="input" placeholder="Or type your answer…" value={comment} onChange={(e) => setComment(e.target.value)} />
            <button disabled={busy || !comment} className="btn btn-primary" onClick={() => send({ answer: comment })}>Send</button>
          </div>
        </>
      )}

      {item.type === "pushback" && (
        <>
          <p className="tile text-[14px] leading-relaxed">{item.reply}</p>
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} className="btn btn-primary" onClick={() => send({ action: "reject" })}>Keep current output</button>
            <button disabled={busy} className="btn btn-ghost" onClick={() => send({ action: "approve" })}>Apply my change anyway</button>
          </div>
        </>
      )}

      {(item.type === "gate" || item.type === "go_live") && (
        <>
          {item.type === "go_live" ? (item.audit ? <AuditView v={item.audit} /> : null) : <Artifact artifact={item.artifact} />}
          {open && (
            <textarea
              className="input min-h-20"
              autoFocus
              placeholder="What should change? e.g. “put more budget on the fitness ad set”"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
          )}
          <div className="flex flex-wrap gap-2">
            <button disabled={busy} className="btn btn-primary" onClick={() => send({ action: "approve" })}>
              {item.type === "go_live" ? <><Rocket size={15} /> Go live</> : <><Check size={15} /> Approve</>}
            </button>
            {open ? (
              <button disabled={busy || !comment} className="btn btn-ghost" onClick={() => send({ action: "changes", comment })}>Send change request</button>
            ) : (
              <button disabled={busy} className="btn btn-ghost" onClick={() => setOpen(true)}><MessageSquare size={15} /> Request changes</button>
            )}
            <button disabled={busy} className="btn btn-danger" onClick={() => send({ action: "reject", comment })}><RotateCcw size={15} /> {item.type === "go_live" ? "Don't launch" : "Redo"}</button>
          </div>
        </>
      )}
    </section>
  );
}
