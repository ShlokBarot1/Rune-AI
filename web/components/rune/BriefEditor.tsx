"use client";
import { useRef, useState } from "react";
import { ImagePlus, Plus, Save, Trash2, Upload, X } from "lucide-react";
import { fileUrl, type Json } from "@/lib/api";
import { editState, reopenNote, uploadImage } from "@/lib/run";

const GOALS = ["sales", "leads", "traffic", "awareness", "engagement", "app_installs"];
const RATIOS = ["1:1", "4:5", "9:16", "1.91:1"];
const SPECIAL = ["none", "credit", "employment", "housing", "social_issues"];
const img = (p?: string) => (!p ? "" : p.startsWith("http") ? p : fileUrl(p));

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1.5"><span className="label">{label}</span>{children}</label>;
}

function UploadButton({ cid, onDone, label }: { cid: string; onDone: (path: string) => void; label: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <input ref={ref} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        if (!f) return;
        setBusy(true);
        try { onDone(await uploadImage(cid, f)); } finally { setBusy(false); e.target.value = ""; }
      }} />
      <button type="button" className="btn btn-ghost !h-9 !text-[12px]" disabled={busy} onClick={() => ref.current?.click()}>
        <Upload size={13} /> {busy ? "Uploading…" : label}
      </button>
    </>
  );
}

/** Edit every brief field, upload a logo / product photos, then save (the run stays at the intake gate). */
export default function BriefEditor({ cid, brief, atGate = true, onSaved, onCancel }: { cid: string; brief: Json; atGate?: boolean; onSaved: () => void; onCancel: () => void }) {
  const [b, setB] = useState<Json>(() => structuredClone(brief));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (path: string, v: Json) => setB((prev: Json) => {
    const next = structuredClone(prev);
    const keys = path.split(".");
    let o = next;
    for (const k of keys.slice(0, -1)) o = o[k] ??= {};
    o[keys[keys.length - 1]] = v;
    return next;
  });
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const save = async () => {
    setBusy(true); setErr(null);
    try { await editState(cid, "brief", b); onSaved(); } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  };

  return (
    <section className="card fade-in flex flex-col gap-5">
      <div className="flex items-center justify-between">
        <div><div className="label">Edit brief</div><div className="text-[18px] font-semibold">Everything the agents will work from</div></div>
        <button className="circle sm" onClick={onCancel} aria-label="Close"><X size={15} /></button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Business name"><input className="input" value={b.business_name ?? ""} onChange={(e) => set("business_name", e.target.value)} /></Field>
        <Field label="Industry"><input className="input" value={b.industry ?? ""} onChange={(e) => set("industry", e.target.value)} /></Field>
      </div>

      <div className="tile flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-3">
          {b.brand?.logo ? <img src={img(b.brand.logo)} alt="" className="h-12 w-12 rounded-xl bg-white object-contain" />
            : <span className="grid h-12 w-12 place-items-center rounded-xl bg-white"><ImagePlus size={18} /></span>}
          <div><div className="title">Logo</div><div className="meta">PNG with transparency works best</div></div>
        </div>
        <UploadButton cid={cid} label={b.brand?.logo ? "Replace logo" : "Upload logo"} onDone={(p) => set("brand.logo", p)} />
        <div className="ml-auto flex items-center gap-2">
          {(b.brand?.colors ?? []).map((c: string, i: number) => (
            <input key={i} type="color" value={c} className="h-9 w-9 cursor-pointer rounded-full border-0 bg-transparent p-0"
              onChange={(e) => set("brand.colors", b.brand.colors.map((x: string, j: number) => (j === i ? e.target.value : x)))} title={c} />
          ))}
          <button className="circle sm" title="Add colour" onClick={() => set("brand.colors", [...(b.brand?.colors ?? []), "#111318"])}><Plus size={14} /></button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="label">Products</span>
          <button className="btn btn-ghost !h-8 !text-[12px]" onClick={() => set("products", [...(b.products ?? []), { name: "New product", usp: [], images: [] }])}><Plus size={13} /> Add product</button>
        </div>
        {(b.products ?? []).map((p: Json, i: number) => (
          <div key={i} className="tile flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              {p.images?.[0] ? <img src={img(p.images[0])} alt="" className="h-14 w-14 rounded-xl bg-white object-cover" />
                : <span className="grid h-14 w-14 place-items-center rounded-xl bg-white"><ImagePlus size={18} /></span>}
              <input className="input !w-auto flex-1" value={p.name} onChange={(e) => set(`products.${i}.name`, e.target.value)} />
              <input className="input !w-28" type="number" placeholder="Price" value={p.price ?? ""} onChange={(e) => set(`products.${i}.price`, e.target.value ? Number(e.target.value) : null)} />
              <UploadButton cid={cid} label="Photo" onDone={(path) => set(`products.${i}.images`, [path, ...(p.images ?? [])])} />
              <button className="circle sm" onClick={() => set("products", b.products.filter((_: Json, j: number) => j !== i))} aria-label="Remove"><Trash2 size={13} /></button>
            </div>
            <input className="input" placeholder="Selling points, comma separated" value={(p.usp ?? []).join(", ")} onChange={(e) => set(`products.${i}.usp`, list(e.target.value))} />
            {(p.images?.length ?? 0) > 1 && (
              <div className="flex gap-2">{p.images.map((im: string, j: number) => <img key={j} src={img(im)} alt="" className="h-10 w-10 rounded-lg object-cover" />)}</div>
            )}
          </div>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Goal"><select className="input" value={b.goal} onChange={(e) => set("goal", e.target.value)}>{GOALS.map((g) => <option key={g}>{g}</option>)}</select></Field>
        <Field label="Budget total"><input className="input" type="number" value={b.budget?.total ?? ""} onChange={(e) => set("budget.total", Number(e.target.value))} /></Field>
        <Field label="Currency"><input className="input" value={b.budget?.currency ?? ""} onChange={(e) => set("budget.currency", e.target.value.toUpperCase())} /></Field>
        <Field label="Start"><input className="input" type="date" value={b.budget?.start ?? ""} onChange={(e) => set("budget.start", e.target.value)} /></Field>
        <Field label="End"><input className="input" type="date" value={b.budget?.end ?? ""} onChange={(e) => set("budget.end", e.target.value)} /></Field>
        <Field label="Countries (ISO)"><input className="input" value={(b.geo ?? []).join(", ")} onChange={(e) => set("geo", list(e.target.value.toUpperCase()))} /></Field>
        <Field label="Image ads"><input className="input" type="number" min={0} value={b.deliverables?.n_images ?? 0} onChange={(e) => set("deliverables.n_images", Number(e.target.value))} /></Field>
        <Field label="Video ads"><input className="input" type="number" min={0} value={b.deliverables?.n_videos ?? 0} onChange={(e) => set("deliverables.n_videos", Number(e.target.value))} /></Field>
        <Field label="Target CPA"><input className="input" type="number" value={b.kpi_targets?.target_cpa ?? ""} onChange={(e) => set("kpi_targets.target_cpa", e.target.value ? Number(e.target.value) : null)} /></Field>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="label mr-1">Ratios</span>
        {RATIOS.map((r) => {
          const on = b.deliverables?.ratios?.includes(r);
          return <button key={r} className={`pill !py-1.5 !px-3 ${on ? "dark" : ""}`} onClick={() => set("deliverables.ratios", on ? b.deliverables.ratios.filter((x: string) => x !== r) : [...(b.deliverables?.ratios ?? []), r])}>{r}</button>;
        })}
        <span className="label ml-4 mr-1">Special category</span>
        <select className="input !w-auto !py-1.5" value={b.special_ad_category} onChange={(e) => set("special_ad_category", e.target.value)}>{SPECIAL.map((s) => <option key={s}>{s}</option>)}</select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Tone of voice"><input className="input" value={b.brand?.tone ?? ""} onChange={(e) => set("brand.tone", e.target.value)} /></Field>
        <Field label="Never say (comma separated)"><input className="input" value={(b.brand?.dont_words ?? []).join(", ")} onChange={(e) => set("brand.dont_words", list(e.target.value))} /></Field>
        <Field label="Audience notes"><textarea className="input min-h-16" value={b.audience_notes ?? ""} onChange={(e) => set("audience_notes", e.target.value)} /></Field>
        <Field label="Landing pages (comma separated)"><textarea className="input min-h-16" value={(b.landing_pages ?? []).join(", ")} onChange={(e) => set("landing_pages", list(e.target.value))} /></Field>
      </div>

      {!atGate && <div className="pill warn !whitespace-normal !text-[12px]">{reopenNote("Intake", false)} Research, strategy, hooks and creatives are redone after you approve it.</div>}
      {err && <div className="pill bad !whitespace-normal !text-[12px]">{err}</div>}
      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={busy} onClick={save}><Save size={14} /> {busy ? "Saving…" : atGate ? "Save brief" : "Save & rebuild from intake"}</button>
        <button className="btn btn-ghost" onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}
