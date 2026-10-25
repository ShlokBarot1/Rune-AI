"use client";
import Link from "next/link";
import { Download, Film, ImageIcon, Images, ShieldCheck } from "lucide-react";
import { fileUrl, type Json } from "@/lib/api";
import { canEdit, useRun } from "@/lib/run";
import { CreativesBoard } from "@/components/rune/CreativesBoard";
import ApprovalDock from "@/components/rune/ApprovalDock";

export default function CreativeStudio() {
  const { runs, clientId, setClientId, snap, reload } = useRun(4000);
  const v = snap?.values ?? {};
  const pkg = v.package;
  const pending = snap?.pending?.[0];
  const gateOpen = pending?.type === "gate" && pending?.gate === "creatives";
  const passed = pkg?.creatives.filter((c: Json) => c.qa?.passed).length ?? 0;
  const brand = v.brief?.business_name ?? "Brand";
  const own = !!pkg?.creatives.length && pkg.creatives.every((c: Json) => c.model_used === "client-upload");

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">Creative Studio</div>
          <h1 className="page-title mt-1">{brand}</h1>
        </div>
        <div className="flex items-center gap-2">
          {runs && runs.length > 1 && (
            <select className="input !w-auto !rounded-full !py-2" value={clientId ?? ""} onChange={(e) => setClientId(e.target.value)} aria-label="Campaign">
              {runs.map((r: Json) => <option key={r.client_id} value={r.client_id}>{r.name}</option>)}
            </select>
          )}
          {pkg && <a className="btn btn-ghost" href={fileUrl(`media/${clientId}/manifest.json`)} target="_blank"><Download size={15} /> Manifest</a>}
        </div>
      </div>

      {!pkg ? (
        <div className="card flex flex-col items-center gap-3 py-16 text-center">
          <Images size={28} />
          <div className="text-[18px] font-semibold">No creatives yet</div>
          <p className="meta">They appear here as soon as the generation step finishes.</p>
          <Link href="/run" className="btn btn-primary">Watch Mission Control</Link>
        </div>
      ) : (
        <>
          <div className="grid items-start gap-4 sm:grid-cols-3">
            <div className="card !p-5" style={{ background: "var(--accent)", borderColor: "transparent" }}>
              <div className="label" style={{ color: "var(--accent-ink)" }}>Assets</div>
              <div className="hero-num mt-2" style={{ color: "var(--accent-ink)" }}>{pkg.creatives.length}</div>
              <div className="meta" style={{ color: "var(--accent-ink)", opacity: 0.7 }}>across {new Set(pkg.creatives.map((c: Json) => c.ratio)).size} ratios</div>
            </div>
            <div className="card !p-5">
              <div className="label flex items-center gap-1.5"><ImageIcon size={12} /> Images · <Film size={12} /> Videos</div>
              <div className="hero-num mt-2">{pkg.creatives.filter((c: Json) => c.kind !== "video").length}<span className="text-[22px] opacity-40"> · </span>{pkg.creatives.filter((c: Json) => c.kind === "video").length}</div>
              <div className="meta">{own ? "your own files, nothing generated" : "videos have voiceover + captions"}</div>
            </div>
            <div className="card !p-5">
              <div className="label flex items-center gap-1.5"><ShieldCheck size={12} /> {own ? "File checks" : "Vision QA"}</div>
              <div className="hero-num mt-2">{passed}<span className="text-[22px] opacity-40">/{pkg.creatives.length}</span></div>
              <div className="mt-2 h-1.5 rounded-full" style={{ background: "var(--bar)" }}>
                <div className="h-1.5 rounded-full" style={{ width: `${(passed / pkg.creatives.length) * 100}%`, background: "var(--ink)" }} />
              </div>
            </div>
          </div>

          {gateOpen && clientId && <ApprovalDock key={pending.interrupt_id} item={pending} clientId={clientId} onDone={reload} />}
          <CreativesBoard pkg={pkg} hooks={v.hooks ?? []} cid={clientId} editable={canEdit(snap)} atGate={gateOpen} onChanged={reload} />
        </>
      )}
    </div>
  );
}
