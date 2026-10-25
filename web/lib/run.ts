"use client";
import { useEffect, useState } from "react";
import { API, authHeaders, post, usePoll, type Json } from "@/lib/api";

/** Save a human edit of Malav-owned state (brief / hooks / package) while the run waits at a gate. */
export const editState = (cid: string, key: "brief" | "hooks" | "package", value: Json) =>
  post(`/malav/runs/${cid}/edit`, { key, value });

/** Upload a file (logo, product photo, or the client's own ad image/video), streamed as the raw body.
 *  Resolves to its media path for the brief / intake. */
export async function uploadFile(cid: string, file: File): Promise<string> {
  const r = await fetch(`${API}/malav/runs/${cid}/upload?filename=${encodeURIComponent(file.name)}`,
    { method: "POST", body: file, headers: authHeaders() });
  if (!r.ok) throw new Error(((await r.json().catch(() => null))?.detail as string) ?? `Upload failed (${r.status})`);
  return (await r.json()).path;
}
export const uploadImage = uploadFile;

/** The client's own creatives go live mid-flight: new ads in an ad set, pending the human's launch approval. */
export const addCreatives = (cid: string, adsetRef: string, creatives: Json[]) =>
  post(`/malav/runs/${cid}/creatives`, { adset_ref: adsetRef, creatives });

/** Send a change request to the waiting gate (the suggestion evaluator + owning agent apply it). */
export const requestChanges = (cid: string, comment: string) => post(`/runs/${cid}/resume`, { action: "changes", comment });

/** Stop the running stage / rerun the current stage after a stop. */
export const stopRun = (cid: string) => post(`/runs/${cid}/stop`, {});
export const continueRun = (cid: string) => post(`/runs/${cid}/continue`, {});

/** Reopen an earlier stage's approval gate; every stage after it reruns once approved. No-op if already there. */
export const rewindTo = (cid: string, stage: string) => post(`/malav/runs/${cid}/rewind`, { stage });

/** Change request to any stage: reopens its gate first if the run has moved past it. */
export async function changeStage(cid: string, stage: string, comment: string) {
  await rewindTo(cid, stage);
  return requestChanges(cid, comment);
}

/** Edits are possible whenever the run waits for a human (not mid-run, not once the ads are live). */
export const canEdit = (snap: Json | null) => !!snap?.pending?.length && !snap.running && !snap.values?.live;

/** What editing a stage does right now, for the confirm line under an editor. */
export const reopenNote = (stage: string, atGate: boolean) =>
  atGate ? "" : `This reopens the ${stage} approval; the stages after it are rebuilt from your edit.`;

/** Every stage of the orchestrated pipeline, in order, with the state key that proves it finished. */
export const STAGES = [
  { id: "intake", label: "Intake", owner: "malav", done: "brief" },
  { id: "research", label: "Research", owner: "malav", done: "research" },
  { id: "strategy", label: "Strategy", owner: "malav", done: "blueprint" },
  { id: "hooks", label: "Hooks", owner: "malav", done: "hooks" },
  { id: "creatives", label: "Creatives", owner: "malav", done: "package" },
  { id: "budget", label: "Budget", owner: "shlok", done: "budget_plan" },
  { id: "targeting", label: "Targeting", owner: "shlok", done: "targeting" },
  { id: "launch", label: "Meta build", owner: "shlok", done: "launch" },
  { id: "audit", label: "Audit", owner: "auditor", done: "audit" },
  { id: "go_live", label: "Go live", owner: "human", done: "live" },
] as const;
export type StageId = (typeof STAGES)[number]["id"];
export type StageState = "todo" | "running" | "waiting" | "done";

/** Which node the graph is on, normalised to a stage id ("hooks__gate" -> hooks, "fix" -> audit). */
function stageOf(node: string | undefined): StageId | null {
  if (!node) return null;
  const n = node.replace(/__gate$/, "");
  if (n === "fix") return "audit";
  if (n === "activate") return "go_live";
  return (STAGES.find((s) => s.id === n)?.id as StageId) ?? null;
}

export function stageStates(snap: Json | null): Record<StageId, StageState> {
  const v = snap?.values ?? {};
  const out = {} as Record<StageId, StageState>;
  const current = stageOf(snap?.next?.[0]);
  const waiting = (snap?.pending?.length ?? 0) > 0;
  for (const s of STAGES) out[s.id] = v[s.done] != null && v[s.done] !== false ? "done" : "todo";
  if (current) {
    const gateOnly = snap?.next?.[0]?.endsWith("__gate");
    // not running and not waiting = stopped (or the API restarted mid-stage): show it as not done
    out[current] = waiting ? "waiting" : gateOnly ? "done" : snap?.running ? "running" : "todo";
  }
  if (v.live) out.go_live = "done";
  return out;
}

export function currentStage(snap: Json | null): StageId {
  return stageOf(snap?.next?.[0]) ?? (snap?.values?.live ? "go_live" : snap?.values?.audit ? "audit" : "intake");
}

/** The run being viewed: ?client=… else the newest run. Polls the snapshot. */
export function useRun(ms = 2500) {
  const [runs] = usePoll<Json[]>("/runs", 5000);
  const [cid, setCid] = useState<string | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("client");
    if (q) setCid(q);
  }, []);
  const id = cid ?? runs?.[0]?.client_id ?? null;
  const [snap, err, reload] = usePoll<Json>(id ? `/runs/${id}` : null, ms);
  return { runs, clientId: id, setClientId: setCid, snap, err, reload };
}

export const AGENT_COLORS: Record<string, [string, string]> = {
  malav: ["#d4f34a", "#1a2200"],
  shlok: ["#9fb7ff", "#0d1a3d"],
  auditor: ["#ffcf7a", "#3a2400"],
  human: ["#ffffff", "#111318"],
  core: ["#3a4150", "#e9edf3"],
};
export function agentColor(sender: string): [string, string] {
  return AGENT_COLORS[sender.split(".")[0]] ?? AGENT_COLORS.core;
}
export function agentShort(sender: string): string {
  const parts = sender.split(".");
  const last = parts[parts.length - 1];
  return last.slice(0, 2).toUpperCase();
}
const STAGE_OF: [RegExp, string][] = [
  [/^malav\.intake/, "Brief"], [/^malav\.research/, "Research"], [/^malav\.strategy/, "Strategy"], [/^malav\.hooks/, "Hooks & copy"],
  [/^malav\.(planner|generator|qa|packager|fixer)/, "Creatives"], [/^shlok\.budget/, "Budget"], [/^shlok\.targeting/, "Targeting"],
  [/^shlok\.launch/, "Meta build"], [/^auditor/, "Audit"], [/^shlok\./, "Live performance"],
];
/** The pipeline stage an agent works in, so the feed groups a stage's agents (e.g. 3 researchers) together. */
export function stageLabel(sender: string): string {
  return STAGE_OF.find(([re]) => re.test(sender))?.[1] ?? agentName(sender);
}

export function agentName(sender: string): string {
  const parts = sender.split(".");
  return parts.length > 1 ? parts.slice(1).join(" · ") : sender;
}
