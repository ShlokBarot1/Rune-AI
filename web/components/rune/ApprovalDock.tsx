"use client";
import { useState } from "react";
import { CalendarDays, Check, Hand, ImagePlus, MessageSquare, Rocket, RotateCcw, Send } from "lucide-react";
import { GATE_TITLES, post, type Json } from "@/lib/api";
import { uploadFile } from "@/lib/run";

const RANGE = /^(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$/;

/** Start/end calendars for a date-range question (its suggestion looks like "2026-10-28 to 2026-11-26");
 *  sends the same "start to end" text the agent already parses. Nothing for other questions. */
export function DateRange({ suggested, busy, onSend }: { suggested?: string; busy: boolean; onSend: (answer: string) => void }) {
  const m = suggested?.match(RANGE);
  const [start, setStart] = useState(m?.[1] ?? "");
  const [end, setEnd] = useState(m?.[2] ?? "");
  if (!m) return null;
  const today = new Date().toISOString().slice(0, 10);
  const ok = !!start && !!end && start >= today && end > start;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <CalendarDays size={16} style={{ color: "var(--text-2)" }} aria-hidden />
      <input type="date" className="input !h-9 !w-auto" aria-label="Start date" value={start} min={today}
        onChange={(e) => setStart(e.target.value)} />
      <span className="meta">to</span>
      <input type="date" className="input !h-9 !w-auto" aria-label="End date" value={end} min={start || today}
        onChange={(e) => setEnd(e.target.value)} />
      <button disabled={busy || !ok} className="btn btn-primary !h-9" onClick={() => onSend(`${start} to ${end}`)}>Use these dates</button>
      {start && end && !ok && <span className="meta">End must be after start, and start can&apos;t be in the past</span>}
    </div>
  );
}

/** What the run is waiting on (gate / question / pushback / go-live), docked at the bottom of the canvas. */
export default function ApprovalDock({ item, clientId, onDone, prefill }: {
  item: Json; clientId: string; onDone: () => void; prefill?: string;
}) {
  const [comment, setComment] = useState(prefill ?? "");
  const [open, setOpen] = useState(!!prefill);
  const [busy, setBusy] = useState(false);
  const [upErr, setUpErr] = useState<string | null>(null);
  const upload = async (files: File[]) => {  // several files: one media path per line
    if (!files.length) return;
    setBusy(true); setUpErr(null);
    try {
      const paths = [];
      for (const f of files) paths.push(await uploadFile(clientId, f));
      await send({ answer: paths.join("\n") });
    } catch (e) { setUpErr(String((e as Error).message)); setBusy(false); }
  };
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
              <button key={o} disabled={busy} className={`btn ${i === 0 ? "btn-lime" : "btn-ghost"} !h-auto min-h-9 !rounded-2xl py-2 !whitespace-normal text-left`} onClick={() => send({ answer: o })}>{o}</button>
            ))}
          </div>
          <DateRange suggested={item.options?.[0]} busy={busy} onSend={(a) => send({ answer: a })} />
          {item.upload && (
            <label className={`btn btn-primary !h-9 w-fit cursor-pointer ${busy ? "opacity-50" : ""}`}>
              <ImagePlus size={14} /> {busy ? "Uploading…" : typeof item.upload === "string" ? item.upload : "Upload photo"}
              <input type="file" multiple accept="image/png,image/jpeg,image/webp" className="hidden" disabled={busy}
                onChange={(e) => { const fs = Array.from(e.target.files ?? []); e.target.value = ""; upload(fs); }} />
            </label>
          )}
          {upErr && <span className="pill bad !whitespace-normal">{upErr}</span>}
          <div className="flex gap-2">
            <input className="input" placeholder={item.upload ? "Or paste an image URL…" : "Or type your own answer…"} value={comment} onChange={(e) => setComment(e.target.value)}
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
