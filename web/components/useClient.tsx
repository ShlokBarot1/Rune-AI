"use client";
import { useEffect, useState } from "react";
import { usePoll, type Json } from "@/lib/api";

/** The client being viewed: ?client=… if given, else the first live run, else the newest run. */
export function useClient() {
  const [runs, err] = usePoll<Json[]>("/runs", 4000);
  const [picked, setPicked] = useState<string | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("client");
    if (q) setPicked(q);
  }, []);
  const id = picked ?? runs?.find((r) => r.live)?.client_id ?? runs?.[0]?.client_id ?? null;
  return { runs, err, clientId: id, setClientId: setPicked };
}

export function ClientPicker({ runs, value, onChange }: { runs: Json[] | null; value: string | null; onChange: (id: string) => void }) {
  if (!runs?.length) return null;
  return (
    <select className="input !w-auto !py-2 !rounded-full" value={value ?? ""} onChange={(e) => onChange(e.target.value)} aria-label="Client">
      {runs.map((r) => <option key={r.client_id} value={r.client_id}>{r.name}{r.live ? " · live" : ""}</option>)}
    </select>
  );
}
