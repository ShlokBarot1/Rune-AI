"use client";
import Link from "next/link";
import { Check, Orbit } from "lucide-react";
import DecisionCard from "@/components/DecisionCard";
import { usePoll, type Json } from "@/lib/api";

export default function Approvals() {
  const [items, err, reload] = usePoll<Json[]>("/approvals", 2500);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Launch &amp; optimise</div>
          <h1 className="page-title mt-1">Approvals</h1>
          <p className="mt-2 max-w-2xl text-[13.5px]" style={{ color: "var(--text-2)" }}>
            Every stage stops here for you. Ask for changes in plain words — the agents check them against the brief,
            Meta policy and the budget before applying, and push back when a change would hurt the campaign.
          </p>
        </div>
        <span className={`pill ${items?.length ? "lime" : ""} !px-3 !py-1.5 !text-[12px]`}>{items?.length ?? 0} waiting</span>
      </div>
      {err && <div className="card label">API unreachable: {err}. Start it with <code>uvicorn api:app --port 8000</code> in backend/.</div>}
      {items?.length === 0 && (
        <div className="card flex flex-wrap items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-full" style={{ background: "var(--bar)" }}><Check size={16} /></span>
          <span className="title flex-1">Nothing waiting — the agents are working or the campaign is live.</span>
          <Link href="/run" className="btn btn-ghost"><Orbit size={15} /> Mission Control</Link>
        </div>
      )}
      {items?.map((it) => <DecisionCard key={it.interrupt_id} item={it} onDone={reload} />)}
    </div>
  );
}
