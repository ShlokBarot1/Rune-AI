"use client";
import { Check } from "lucide-react";
import DecisionCard from "@/components/DecisionCard";
import { usePoll, type Json } from "@/lib/api";

export default function Approvals() {
  const [items, err, reload] = usePoll<Json[]>("/approvals", 2500);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <div>
        <h1 className="text-[28px] tracking-tight" style={{ fontWeight: 500 }}>Approvals</h1>
        <p className="label mt-1">Every stage stops here for you. Request changes in plain words — the agents check them against the brief, Meta policy and the budget before applying.</p>
      </div>
      {err && <div className="card label">API unreachable: {err}. Start it with <code>uvicorn api:app --port 8000</code> in backend/.</div>}
      {items?.length === 0 && (
        <div className="card flex items-center gap-3">
          <span className="circle"><Check size={16} /></span>
          <span className="title">Nothing waiting. Agents are working or campaigns are live.</span>
        </div>
      )}
      {items?.map((it) => <DecisionCard key={it.interrupt_id} item={it} onDone={reload} />)}
    </div>
  );
}
