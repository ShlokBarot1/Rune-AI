"use client";
import { useEffect, useRef, useState } from "react";

export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any;

/** Session token (agency mode, RUNE_AUTH=1); absent when the API runs open. */
export const token = () => { try { return localStorage.getItem("rune_token") ?? ""; } catch { return ""; } };
export const authHeaders = (): Record<string, string> => (token() ? { Authorization: `Bearer ${token()}` } : {});

/** A 401 means the session is gone: back to sign-in (except on pages that are public). */
function signedOut(r: Response) {
  if (r.status === 401 && typeof window !== "undefined" && !/^\/(login|share)/.test(window.location.pathname)) {
    try { localStorage.removeItem("rune_token"); } catch {}
    window.location.href = "/login";
  }
}

async function fail(r: Response): Promise<never> {
  signedOut(r);
  const body = await r.text();
  let msg = body;
  try { msg = JSON.parse(body).detail ?? body; } catch {}
  throw new Error(`${r.status} ${typeof msg === "string" ? msg : JSON.stringify(msg)}`);
}

export async function get<T = Json>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: "no-store", headers: authHeaders() });
  if (!r.ok) return fail(r);
  return r.json();
}

export async function post<T = Json>(path: string, body: Json = {}): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) return fail(r);
  return r.json();
}

/** An API page (report) opened in a new tab: the token rides in the URL since tabs don't send headers. */
export const apiPage = (path: string) => `${API}${path}${token() ? `${path.includes("?") ? "&" : "?"}token=${token()}` : ""}`;

export const fileUrl = (path: string) => `${API}/files/${path.replace(/^\.?\//, "")}`;

/** Poll an endpoint; returns [data, error, reload]. */
export function usePoll<T = Json>(path: string | null, ms = 3000): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true, inFlight = false;
    const load = () => {
      // background tabs and slow responses don't stack requests (every page polls several endpoints)
      if (inFlight || document.hidden) return;
      inFlight = true;
      get<T>(path)
        .then((d) => alive && (setData(d), setErr(null)))
        .catch((e) => alive && setErr(String(e.message ?? e)))
        .finally(() => { inFlight = false; });
    };
    load();
    const id = setInterval(load, ms);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", load);
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
    const t = token() ? `${q ? "&" : "?"}token=${token()}` : "";  // EventSource can't send headers
    const es = new EventSource(`${API}/events${q}${t}`);
    es.addEventListener("message", (e) => {
      buf.current = [...buf.current, JSON.parse((e as MessageEvent).data)].slice(-keep);
      setMsgs(buf.current);
    });
    return () => es.close();
  }, [clientId, keep]);
  return msgs;
}

const CHECKS: Record<string, string> = {
  has_text_layer: "headline and button missing", exact_size: "wrong size", file_exists: "file missing",
  duration_ok: "video length off", thumbnail: "no cover image", size_ok: "file too large", safe_zones: "text in unsafe area",
};
const ACTION: Record<string, string> = {
  pause_ad: "Paused", scale_adset: "More budget for", cut_adset: "Less budget for",
  refresh_creatives: "New creatives requested for", launch_creatives: "New creatives ready for",
};
const TEAM: Record<string, string> = { malav: "creative team", shlok: "media-buying team" };
const DECISION: Record<string, string> = { PASS: "passed", PASS_WITH_NOTES: "passed with notes", FAIL: "failed" };

/** Agent text in plain words: internal ids, tool names and check codes become things a client recognises. */
export function humanize(t: string): string {
  return t
    .replace(/\bcr_ad_[a-z0-9_]+?_(\d+)_(\d+(?:\.\d+)?)x(\d+)\b/g, "Ad $1 ($2:$3)")
    .replace(/\bcr_[0-9a-f]{8,}\b/g, "a new variant")
    .replace(/\bad_[a-z0-9_]+?_(\d+)\b/g, "Ad $1")
    .replace(/\bas_[a-z0-9_]+\b/g, "the ad set")
    .replace(/\s*\(?\bact_[0-9a-z]+\)?/g, "")
    .replace(/\s*\((?:skills|templates?): [^)]*\)/g, "")
    .replace(/ with Jina Reader| \(rembg\)/g, "")
    .replace(/\s*\((?:\w+Error|\w+Exception)\)/g, "")
    .replace(/Failed checks: ([\w, ]+)/g, (_, c: string) => c.split(", ").map((k) => CHECKS[k] ?? k.replace(/_/g, " ")).join(", "))
    .replace(/\bfailed QA\b/g, "failed the quality check")
    .replace(/\bNEEDS HUMAN\b/g, "needs your review")
    .replace(/\bscore (\d+)\b/g, "score $1/100")
    .replace(/\bPAUSED\b/g, "paused");
}

function raw(p: Json): string {
  if (p.tool === "budget_feasibility") return `Can the budget support ${p.n_adsets} ad set${p.n_adsets === 1 ? "" : "s"}?`;
  if (p.feasible != null && p.suggestion) return `${p.feasible ? "Yes" : "No"}: ${String(p.suggestion).replace(/^(OK|NO):\s*/i, "")}`;
  if (p.decision) {
    const weak = Object.entries(p.scores ?? {}).filter(([, v]) => (v as number) < 100)
      .sort((a, b) => (a[1] as number) - (b[1] as number)).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}/100`);
    return `Audit ${DECISION[p.decision] ?? p.decision}: ${p.n_findings ?? 0} issue(s)` + (weak.length ? `. Weakest: ${weak.join(", ")}` : "");
  }
  if (p.kind && p.ref && p.reason != null) return `${ACTION[p.kind] ?? String(p.kind).replace(/_/g, " ")} ${p.ref}` +
    (p.reason ? `: ${p.reason}` : "") + (p.status && p.status !== "applied" ? ` (${p.status})` : "");
  if (p.object_refs && p.prompt) return `Sent ${new Set(p.object_refs).size} fix(es) to the ${TEAM[p.owner] ?? p.owner}`;
  if (p.report) return String(p.report).split("\n\n").find((x) => x.length > 40)?.slice(0, 220) ?? String(p.report).slice(0, 220);
  return (
    p.text ?? p.question ?? p.reply ?? p.reason ?? p.suggestion ?? p.insight ?? p.message ?? p.error ??
    (p.action ? `${p.action}${p.comment ? `: ${p.comment}` : ""}` : null) ??
    JSON.stringify(p).slice(0, 160)
  );
}

export function msgText(m: BusMsg): string {
  return humanize(raw(m.payload ?? {}));
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
