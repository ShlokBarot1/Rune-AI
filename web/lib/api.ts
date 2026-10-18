"use client";
import { useEffect, useRef, useState } from "react";

export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any;

export async function get<T = Json>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export async function post<T = Json>(path: string, body: Json = {}): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const fileUrl = (path: string) => `${API}/files/${path.replace(/^\.?\//, "")}`;

/** Poll an endpoint; returns [data, error, reload]. */
export function usePoll<T = Json>(path: string | null, ms = 3000): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () =>
      get<T>(path)
        .then((d) => alive && (setData(d), setErr(null)))
        .catch((e) => alive && setErr(String(e.message ?? e)));
    load();
    const id = setInterval(load, ms);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path, ms, tick]);
  return [data, err, () => setTick((t) => t + 1)];
}

export type BusMsg = {
  seq: number;
  client_id: string;
  sender: string;
  recipient: string;
  type: string;
  payload: Json;
  created_at: string;
};

/** Live agent bus over SSE. */
export function useEvents(clientId?: string | null, keep = 300): BusMsg[] {
  const [msgs, setMsgs] = useState<BusMsg[]>([]);
  const buf = useRef<BusMsg[]>([]);
  useEffect(() => {
    buf.current = [];
    setMsgs([]);
    const q = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
    const es = new EventSource(`${API}/events${q}`);
    es.addEventListener("message", (e) => {
      buf.current = [...buf.current, JSON.parse((e as MessageEvent).data)].slice(-keep);
      setMsgs(buf.current);
    });
    return () => es.close();
  }, [clientId, keep]);
  return msgs;
}

export function msgText(m: BusMsg): string {
  const p = m.payload ?? {};
  return (
    p.text ??
    p.question ??
    p.reply ??
    p.reason ??
    p.suggestion ??
    p.insight ??
    (p.tool ? `calls ${p.tool}(${p.n_adsets ?? ""})` : null) ??
    p.message ??
    p.error ??
    (p.report ? String(p.report).slice(0, 160) : null) ??
    (p.decision ? `Audit ${p.decision}` : null) ??
    (p.action ? `${p.action}${p.comment ? `: ${p.comment}` : ""}` : null) ??
    JSON.stringify(p).slice(0, 160)
  );
}

export const money = (n: number | null | undefined, cur = "INR") =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en-IN", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(n);

export const pct = (n: number | null | undefined, d = 2) => (n == null ? "—" : `${(n * 100).toFixed(d)}%`);

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export const GATE_TITLES: Record<string, string> = {
  intake: "Client brief",
  research: "Research report",
  strategy: "Campaign blueprint",
  hooks: "Hooks & copy",
  creatives: "Creatives",
  budget: "Budget plan",
  targeting: "Targeting & placements",
  go_live: "Final audit · Go live",
};
