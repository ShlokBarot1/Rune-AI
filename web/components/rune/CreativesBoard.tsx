"use client";
import { useMemo, useState } from "react";
import { Film, ImageIcon, RefreshCw } from "lucide-react";
import type { Json } from "@/lib/api";
import { MetaAdMock, PhoneFrame, QAChip } from "@/components/rune/Views";
import { fileUrl } from "@/lib/api";

/** Creatives grouped by ad set with ratio tabs. onRegenerate is only passed while the creatives gate is open. */
export function CreativesBoard({ pkg, compact = false, onRegenerate }: {
  pkg: Json; compact?: boolean; onRegenerate?: (c: Json) => void;
}) {
  const ratios: string[] = useMemo(() => Array.from(new Set(pkg.creatives.map((c: Json) => c.ratio))), [pkg]);
  const [ratio, setRatio] = useState<string>("all");
  const [kind, setKind] = useState<string>("all");
  const adsets: Json[] = pkg.blueprint.campaigns.flatMap((c: Json) => c.adsets);
  const brand = pkg.client_id;
  const show = (c: Json) => (ratio === "all" || c.ratio === ratio) && (kind === "all" || c.kind === kind);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="seg">
          {["all", ...ratios].map((r) => <button key={r} className={ratio === r ? "on" : ""} onClick={() => setRatio(r)}>{r === "all" ? "All ratios" : r}</button>)}
        </div>
        <div className="seg">
          {["all", "image", "video"].map((k) => <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{k === "all" ? "All" : k === "image" ? "Images" : "Videos"}</button>)}
        </div>
      </div>
      {adsets.map((s) => {
        const items = pkg.creatives.filter((c: Json) => c.adset_ref === s.ref && show(c));
        if (!items.length) return null;
        return (
          <section key={s.ref} className="card">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div><div className="text-[16px] font-semibold">{s.name}</div><div className="meta">{s.angle}</div></div>
              <span className="pill">{items.length} assets</span>
            </div>
            <div className={`mt-4 grid gap-5 ${compact ? "grid-cols-2 md:grid-cols-3 2xl:grid-cols-4" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4"}`}>
              {items.map((c: Json) => (
                <div key={c.creative_id} className="flex flex-col items-center gap-3 fade-in">
                  <PhoneFrame c={c} width={compact ? 170 : 210} />
                  <div className="flex w-full flex-col gap-1.5" style={{ maxWidth: compact ? 190 : 230 }}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="pill dark">{c.kind === "video" ? <Film size={11} /> : <ImageIcon size={11} />}{c.ratio}</span>
                      <QAChip qa={c.qa} />
                      {c.parent_id && <span className="pill lime">v{c.version} · iteration</span>}
                      {c.duration_s && <span className="pill">{c.duration_s}s</span>}
                    </div>
                    {!compact && (
                      <>
                        <div className="text-[12.5px] font-semibold leading-snug">{c.ad_copy.headline}</div>
                        {c.qa?.notes && <div className="meta leading-snug">{c.qa.notes}</div>}
                        <div className="meta">{c.template_id} · {c.model_used}</div>
                      </>
                    )}
                    {onRegenerate && (
                      <button className="btn btn-ghost !h-8 !text-[12px]" onClick={() => onRegenerate(c)}><RefreshCw size={13} /> Regenerate</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

/** Feed-style preview of one creative with its copy. */
export function FeedPreview({ c, brand }: { c: Json; brand: string }) {
  return <MetaAdMock brand={brand} copy={c.ad_copy} image={c.kind === "image" ? fileUrl(c.path) : c.thumbnail_path ? fileUrl(c.thumbnail_path) : null} />;
}
