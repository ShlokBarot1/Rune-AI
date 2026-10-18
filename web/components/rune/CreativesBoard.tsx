"use client";
import { useEffect, useMemo, useState } from "react";
import { Check, Download, ExternalLink, Film, ImageIcon, RefreshCw, Save, X } from "lucide-react";
import { fileUrl, type Json } from "@/lib/api";
import { editState, requestChanges } from "@/lib/run";
import { QAChip } from "@/components/rune/Views";

const CTAS = ["SHOP_NOW", "LEARN_MORE", "SIGN_UP", "ORDER_NOW", "GET_OFFER", "BOOK_NOW", "CONTACT_US", "DOWNLOAD", "SUBSCRIBE", "APPLY_NOW"];
const ctaLabel = (c: string) => c.replace(/_/g, " ").toLowerCase().replace(/^./, (x) => x.toUpperCase());

function Thumb({ c, onOpen }: { c: Json; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="group flex flex-col gap-2 text-left fade-in">
      <div className="relative overflow-hidden rounded-[18px] bg-black shadow-sm transition group-hover:-translate-y-0.5 group-hover:shadow-lg"
        style={{ aspectRatio: `${c.width}/${c.height}` }}>
        <img src={fileUrl(c.kind === "video" && c.thumbnail_path ? c.thumbnail_path : c.path)} alt={c.creative_id} className="h-full w-full object-cover" />
        {c.kind === "video" && <span className="absolute right-2 top-2 pill dark"><Film size={11} /> {c.duration_s}s</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="pill dark">{c.kind === "video" ? <Film size={11} /> : <ImageIcon size={11} />}{c.ratio}</span>
        <QAChip qa={c.qa} />
        {c.parent_id && <span className="pill lime">iteration v{c.version}</span>}
      </div>
      <div className="line-clamp-2 text-[12.5px] font-semibold leading-snug">{c.ad_copy.headline}</div>
    </button>
  );
}

function Counter({ n, max }: { n: number; max: number }) {
  return <span className="meta" style={n > max ? { color: "var(--danger)" } : undefined}>{n}/{max}</span>;
}

/** Full creative: asset, hook, editable copy, QA, tracking; save copy / regenerate while the creatives gate is open. */
export function CreativeModal({ c, pkg, hooks, cid, editable, onClose, onChanged }: {
  c: Json; pkg: Json; hooks: Record<string, Json>; cid: string | null; editable: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [copy, setCopy] = useState<Json>(() => ({ ...c.ad_copy }));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const hook = hooks[c.hook_id];
  const siblings = pkg.creatives.filter((x: Json) => x.ad_ref === c.ad_ref).length;

  const save = async () => {
    if (!cid) return;
    setBusy("save");
    try {  // copy belongs to the Meta ad, so every ratio of this ad gets it
      const next = { ...pkg, creatives: pkg.creatives.map((x: Json) => (x.ad_ref === c.ad_ref ? { ...x, ad_copy: copy } : x)) };
      await editState(cid, "package", next);
      setMsg(`Saved on ${siblings} asset${siblings > 1 ? "s" : ""} of this ad.`); onChanged();
    } catch (e) { setMsg(String((e as Error).message)); } finally { setBusy(null); }
  };
  const regenerate = async () => {
    if (!cid) return;
    setBusy("regen");
    try { await requestChanges(cid, `Regenerate ${c.creative_id}: ${note || "improve it based on the QA notes"}`); setMsg("Sent — the agents are regenerating this asset."); onChanged(); }
    catch (e) { setMsg(String((e as Error).message)); } finally { setBusy(null); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(12,15,20,.55)", backdropFilter: "blur(6px)" }} onClick={onClose}>
      <div className="card !p-0 grid max-h-[92vh] w-full max-w-6xl overflow-hidden md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]" style={{ background: "rgba(255,255,255,.97)" }} onClick={(e) => e.stopPropagation()}>
        <div className="grid place-items-center p-5" style={{ background: "#0d0f13" }}>
          {c.kind === "video"
            ? <video src={fileUrl(c.path)} poster={c.thumbnail_path ? fileUrl(c.thumbnail_path) : undefined} controls autoPlay loop playsInline className="max-h-[82vh] w-auto rounded-xl" style={{ aspectRatio: `${c.width}/${c.height}` }} />
            : <img src={fileUrl(c.path)} alt="" className="max-h-[82vh] w-auto rounded-xl" style={{ aspectRatio: `${c.width}/${c.height}` }} />}
        </div>
        <div className="flex flex-col gap-4 overflow-y-auto p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="label">{c.adset_ref} · {c.ad_ref}</div>
              <div className="text-[18px] font-semibold">{c.kind === "video" ? "Video" : "Image"} · {c.ratio} · {c.width}×{c.height}</div>
            </div>
            <button className="circle sm" onClick={onClose} aria-label="Close"><X size={15} /></button>
          </div>

          <div className="tile">
            <div className="label">Hook {hook?.framework ? `· ${hook.framework}` : ""} {hook?.critic_score ? `· critic ${hook.critic_score}/10` : ""}</div>
            <div className="mt-1 text-[15px] font-semibold leading-snug">“{hook?.text ?? c.ad_copy.primary_text.split(/[.!?]/)[0]}”</div>
          </div>

          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="flex justify-between"><span className="label">Primary text · first 125 chars show</span><Counter n={copy.primary_text.length} max={250} /></span>
              <textarea className="input min-h-24" disabled={!editable} value={copy.primary_text} onChange={(e) => setCopy({ ...copy, primary_text: e.target.value })} />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="flex justify-between"><span className="label">Headline</span><Counter n={copy.headline.length} max={40} /></span>
                <input className="input" disabled={!editable} value={copy.headline} onChange={(e) => setCopy({ ...copy, headline: e.target.value })} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="flex justify-between"><span className="label">Description</span><Counter n={(copy.description ?? "").length} max={30} /></span>
                <input className="input" disabled={!editable} value={copy.description ?? ""} onChange={(e) => setCopy({ ...copy, description: e.target.value })} />
              </label>
            </div>
            <label className="flex flex-col gap-1">
              <span className="label">Call to action</span>
              <select className="input" disabled={!editable} value={copy.cta} onChange={(e) => setCopy({ ...copy, cta: e.target.value })}>
                {CTAS.map((x) => <option key={x} value={x}>{ctaLabel(x)}</option>)}
              </select>
            </label>
          </div>

          <div className="tile flex flex-col gap-2">
            <div className="flex items-center justify-between"><span className="label">Vision QA</span><QAChip qa={c.qa} /></div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries<boolean>(c.qa?.checks ?? {}).map(([k, v]) => (
                <span key={k} className={`pill ${v ? "ok" : "bad"}`}>{v ? <Check size={11} /> : <X size={11} />}{k.replace(/_/g, " ")}</span>
              ))}
            </div>
            {c.qa?.notes && <p className="text-[12.5px] leading-relaxed" style={{ color: "var(--text-2)" }}>{c.qa.notes}</p>}
          </div>

          <div className="flex flex-col gap-1 text-[12px]" style={{ color: "var(--text-2)" }}>
            <div><b>Template</b> {c.template_id} · <b>Background</b> {c.model_used}</div>
            <a className="inline-flex items-center gap-1 break-all hover:underline" href={c.landing_url} target="_blank"><ExternalLink size={11} />{c.landing_url}</a>
          </div>

          {editable ? (
            <div className="flex flex-col gap-2 border-t pt-4" style={{ borderColor: "var(--line)" }}>
              <div className="flex flex-wrap gap-2">
                <button className="btn btn-primary" disabled={!!busy} onClick={save}><Save size={14} /> {busy === "save" ? "Saving…" : "Save copy"}</button>
                <a className="btn btn-ghost" href={fileUrl(c.path)} download><Download size={14} /> Download</a>
              </div>
              <div className="flex gap-2">
                <input className="input" placeholder="Regenerate with a note — e.g. “bigger product, warmer light”" value={note} onChange={(e) => setNote(e.target.value)} />
                <button className="btn btn-lime" disabled={!!busy} onClick={regenerate}><RefreshCw size={14} /> Regenerate</button>
              </div>
            </div>
          ) : (
            <div className="meta border-t pt-3" style={{ borderColor: "var(--line)" }}>Editing opens at the Creatives approval gate. <a className="underline" href={fileUrl(c.path)} download>Download asset</a></div>
          )}
          {msg && <div className="pill blue !whitespace-normal !text-[12px]">{msg}</div>}
        </div>
      </div>
    </div>
  );
}

/** Creatives grouped by ad set with ratio/format tabs; click a creative for the full view. */
export function CreativesBoard({ pkg, compact = false, hooks = [], cid = null, editable = false, onChanged }: {
  pkg: Json; compact?: boolean; hooks?: Json[]; cid?: string | null; editable?: boolean; onChanged?: () => void;
}) {
  const ratios: string[] = useMemo(() => Array.from(new Set(pkg.creatives.map((c: Json) => c.ratio))), [pkg]);
  const [ratio, setRatio] = useState<string>("all");
  const [kind, setKind] = useState<string>("all");
  const [open, setOpen] = useState<Json | null>(null);
  const hookMap = useMemo(() => Object.fromEntries(hooks.map((h: Json) => [h.id, h])), [hooks]);
  const adsets: Json[] = pkg.blueprint.campaigns.flatMap((c: Json) => c.adsets);
  const show = (c: Json) => (ratio === "all" || c.ratio === ratio) && (kind === "all" || c.kind === kind);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="seg">{["all", ...ratios].map((r) => <button key={r} className={ratio === r ? "on" : ""} onClick={() => setRatio(r)}>{r === "all" ? "All ratios" : r}</button>)}</div>
        <div className="seg">{["all", "image", "video"].map((k) => <button key={k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>{k === "all" ? "All" : k === "image" ? "Images" : "Videos"}</button>)}</div>
        <span className="meta ml-auto">Click any creative to see its hook, copy and QA{editable ? " — and edit it" : ""}</span>
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
            <div className={`mt-4 grid items-end gap-5 ${compact ? "grid-cols-2 md:grid-cols-4" : "grid-cols-2 md:grid-cols-3 xl:grid-cols-4"}`}>
              {items.map((c: Json) => <Thumb key={c.creative_id} c={c} onOpen={() => setOpen(c)} />)}
            </div>
          </section>
        );
      })}
      {open && <CreativeModal c={open} pkg={pkg} hooks={hookMap} cid={cid} editable={editable} onClose={() => setOpen(null)} onChanged={() => onChanged?.()} />}
    </div>
  );
}
