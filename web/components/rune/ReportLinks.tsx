"use client";
import { useState } from "react";
import { FileText, Link2 } from "lucide-react";
import { API, apiPage, post } from "@/lib/api";

/** Report + share buttons, shared with Live Performance. */
export default function ReportLinks({ cid }: { cid: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  const share = async () => {
    try {
      const { path } = await post(`/runs/${cid}/share`, {});
      const url = `${API}${path}`;
      await navigator.clipboard?.writeText(url).catch(() => {});
      setMsg(`Link copied (valid 30 days): ${url}`);
    } catch (e) { setMsg(String((e as Error).message)); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <a className="btn btn-ghost !h-9" href={apiPage(`/runs/${cid}/report.html?days=7`)} target="_blank" rel="noreferrer"><FileText size={14} /> Weekly report</a>
      <button className="btn btn-ghost !h-9" onClick={share}><Link2 size={14} /> Share link</button>
      {msg && <span className="meta break-all">{msg}</span>}
    </div>
  );
}

