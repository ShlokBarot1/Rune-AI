"use client";
import { useState } from "react";
import { ArrowUpRight, CheckCircle2, ExternalLink, Film, Globe, ImageIcon, Pencil, Quote, Sparkles, Star, Target, TrendingUp, XCircle } from "lucide-react";
import { fileUrl, money, type Json } from "@/lib/api";

const cta = (c?: string) => (c ? c.replace(/_/g, " ").toLowerCase().replace(/^./, (x) => x.toUpperCase()) : "Learn more");
const Empty = ({ text }: { text: string }) => <div className="tile py-10 text-center text-[13px]" style={{ color: "var(--text-2)" }}>{text}</div>;

/* ---------------- brief ---------------- */
export function BriefCard({ b, live = false, onEdit }: { b: Json | null; live?: boolean; onEdit?: () => void }) {
  if (!b) return <Empty text="The brief fills in here as the intake agent reads the site." />;
  const p0 = b.products?.[0];
  const budget = b.budget ?? {};
  const slot = (label: string, value: React.ReactNode, ok = true) => (
    <div className="tile flex flex-col gap-1">
      <div className="label flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: ok ? "var(--success)" : "var(--text-3)" }} />{label}
      </div>
      <div className="text-[14px] font-semibold">{value ?? <span className="meta">asking…</span>}</div>
    </div>
  );
  return (
    <section className={`card flex flex-col gap-4 ${live ? "fade-in" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="label flex items-center gap-1.5"><Globe size={12} /> {b.website ?? "client brief"}</div>
          <div className="page-title !text-[28px] mt-1">{b.business_name || "—"}</div>
          <div className="meta mt-1">{b.industry}</div>
        </div>
        <div className="flex items-center gap-3">
          {b.brand?.logo && <img src={b.brand.logo.startsWith("http") ? b.brand.logo : fileUrl(b.brand.logo)} alt="" className="h-12 max-w-[120px] object-contain" />}
          {onEdit && <button className="btn btn-ghost !h-9" onClick={onEdit}><Pencil size={14} /> Edit brief</button>}
        </div>
      </div>
      {!!b.brand?.colors?.length && (
        <div className="flex items-center gap-2">
          {b.brand.colors.slice(0, 5).map((c: string) => (
            <span key={c} className="h-7 w-7 rounded-full border border-white shadow" style={{ background: c }} title={c} />
          ))}
          {b.brand?.tone && <span className="pill ml-1">{b.brand.tone}</span>}
        </div>
      )}
      {!!b.products?.length && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {b.products.slice(0, 3).map((p: Json) => (
            <div key={p.name} className="tile flex items-center gap-2 !p-2">
              {p.images?.[0] ? <img src={p.images[0].startsWith("http") ? p.images[0] : fileUrl(p.images[0])} alt="" className="h-11 w-11 rounded-xl object-cover bg-white" />
                : <span className="grid h-11 w-11 place-items-center rounded-xl bg-white"><ImageIcon size={16} /></span>}
              <div className="min-w-0">
                <div className="truncate text-[12px] font-semibold">{p.name}</div>
                <div className="meta">{p.price ? money(p.price, budget.currency ?? "INR") : "—"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {slot("Goal", b.goal)}
        {slot("Budget", budget.total ? money(budget.total, budget.currency) : null, !!budget.total)}
        {slot("Flight", budget.start ? `${budget.start} → ${budget.end}` : null, !!budget.start)}
        {slot("Geo", b.geo?.join(", "))}
        {slot("Deliverables", b.deliverables ? `${b.deliverables.n_images} img · ${b.deliverables.n_videos} vid` : null, !!b.deliverables)}
        {slot("Target CPA", b.kpi_targets?.target_cpa ? money(b.kpi_targets.target_cpa, budget.currency) : "not set", !!b.kpi_targets?.target_cpa)}
      </div>
      {p0?.usp?.length > 0 && <div className="flex flex-wrap gap-1.5">{p0.usp.slice(0, 4).map((u: string) => <span key={u} className="pill blue">{u}</span>)}</div>}
    </section>
  );
}

/* ---------------- research ---------------- */
export function ResearchView({ r }: { r: Json | null }) {
  if (!r) return <Empty text="Three research agents are working in parallel — personas, competitors and trends land here." />;
  return (
    <div className="flex flex-col gap-4 fade-in">
      <section className="card">
        <div className="label">Executive summary</div>
        <p className="mt-2 text-[15px] leading-relaxed">{r.summary}</p>
        {!!r.recommended_angles?.length && (
          <div className="mt-4 flex flex-col gap-2">
            {r.recommended_angles.slice(0, 5).map((a: string, i: number) => (
              <div key={i} className="flex items-start gap-3 tile !py-2.5">
                <span className="pill lime">#{i + 1}</span><span className="text-[13px]">{a}</span>
              </div>
            ))}
          </div>
        )}
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        {r.personas?.map((p: Json) => (
          <section key={p.ref} className="card flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-[16px] font-semibold">{p.name}</div>
                <div className="meta">{p.age_range?.join("–")} · {p.genders?.join(", ")}</div>
              </div>
              <span className="pill">{p.evidence?.length ?? 0} sources</span>
            </div>
            <p className="text-[13px]" style={{ color: "var(--text-2)" }}>{p.description}</p>
            {p.phrases?.slice(0, 2).map((q: string) => (
              <div key={q} className="bubble light-left !max-w-full flex gap-2"><Quote size={13} className="mt-0.5 shrink-0 opacity-50" />{q}</div>
            ))}
            <div className="flex flex-wrap gap-1.5">
              {p.pains?.slice(0, 3).map((x: string) => <span key={x} className="pill bad">{x}</span>)}
              {p.desires?.slice(0, 3).map((x: string) => <span key={x} className="pill ok">{x}</span>)}
            </div>
            <div className="flex flex-wrap gap-1.5">{p.interests?.slice(0, 5).map((x: string) => <span key={x} className="pill blue">{x}</span>)}</div>
            {!!p.evidence?.length && (
              <div className="flex flex-wrap gap-2">
                {p.evidence.slice(0, 3).map((u: string) => (
                  <a key={u} href={u} target="_blank" className="meta inline-flex items-center gap-1 hover:underline"><ExternalLink size={11} />{new URL(u).hostname.replace("www.", "")}</a>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
      <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
        <section className="card">
          <div className="label flex items-center gap-1.5"><Target size={12} /> Competitor matrix</div>
          <div className="mt-2">
            {r.competitors?.map((c: Json) => (
              <div key={c.name} className="row !items-start">
                <div className="w-32 shrink-0 text-[13px] font-semibold">{c.name}</div>
                <div className="flex flex-wrap gap-1.5">{(c.angles?.length ? c.angles : ["angles unknown"]).map((a: string) => <span key={a} className="pill">{a}</span>)}</div>
              </div>
            ))}
            {!r.competitors?.length && <div className="meta py-4">No competitors found.</div>}
          </div>
        </section>
        <section className="card">
          <div className="label flex items-center gap-1.5"><TrendingUp size={12} /> Trends & seasonal hooks</div>
          <div className="mt-2 flex flex-col gap-2">
            {r.trends?.slice(0, 6).map((t: string) => <div key={t} className="tile !py-2 text-[12.5px]">{t}</div>)}
            {!r.trends?.length && <div className="meta py-4">No trend signals.</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

/* ---------------- strategy ---------------- */
export function StrategyView({ bp, negotiation }: { bp: Json | null; negotiation: React.ReactNode }) {
  if (!bp) return <Empty text="The strategy agent is designing the campaign and negotiating budget with shlok.budget." />;
  const all: Json[] = bp.campaigns?.flatMap((c: Json) => c.adsets) ?? [];
  const nAds = all.reduce((n, s) => n + (s.ads?.length ?? 0), 0);
  return (
    <div className="flex flex-col gap-4 fade-in">
      {bp.campaigns?.map((c: Json) => (
        <section key={c.ref} className="rounded-[26px] p-5 text-white" style={{ background: "var(--ink)" }}>
          <div className="label" style={{ color: "#9aa3b2" }}>Campaign</div>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-[22px] font-semibold tracking-tight">{c.name}</span>
            <span className="pill lime">{c.objective.replace("OUTCOME_", "")}</span><span className="pill dark">{c.funnel_stage} funnel</span>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3 sm:max-w-md">
            {[["Ad sets", c.adsets.length], ["Ads", nAds], ["Formats", Array.from(new Set(all.flatMap((s) => s.ads.map((a: Json) => a.format)))).join(" + ")]].map(([k, val]) => (
              <div key={k as string}><div className="text-[11px]" style={{ color: "#9aa3b2" }}>{k}</div><div className="text-[20px] font-semibold capitalize">{val}</div></div>
            ))}
          </div>
        </section>
      ))}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="grid gap-4 md:grid-cols-2">
          {all.map((s: Json, i: number) => {
            const [why, extra] = String(s.rationale ?? "").split(/Also test as hooks:/);
            return (
              <section key={s.ref} className="card flex flex-col gap-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="pill lime">Ad set {i + 1}</span><span className="pill blue">{s.persona_ref.replace(/^p_/, "")}</span>
                </div>
                <div>
                  <div className="label">Angle</div>
                  <div className="text-[17px] font-semibold leading-snug">{s.angle}</div>
                </div>
                {why?.trim() && <p className="text-[12.5px] leading-relaxed" style={{ color: "var(--text-2)" }}>{why.trim()}</p>}
                {extra && <div className="flex flex-wrap items-center gap-1.5"><span className="label">Also tests</span>{extra.split(/[.;]/).map((x) => x.trim()).filter(Boolean).map((x) => <span key={x} className="pill">{x}</span>)}</div>}
                <div className="mt-auto grid grid-cols-2 gap-2">
                  {s.ads?.map((a: Json, j: number) => (
                    <div key={a.ref} className="tile flex items-center gap-2 !p-2.5">
                      <span className="grid h-8 w-8 place-items-center rounded-xl bg-white">{a.format === "video" ? <Film size={14} /> : <ImageIcon size={14} />}</span>
                      <div className="min-w-0 leading-tight"><div className="whitespace-nowrap text-[12.5px] font-semibold capitalize">{a.format} {j + 1}</div><div className="meta whitespace-nowrap">{a.ratios?.join(" · ")}</div></div>
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
        <section className="card self-start">
          <div className="label">Negotiation · strategy ⇄ budget</div>
          <div className="mt-3 flex flex-col gap-2">{negotiation}</div>
          {bp.rationale && <p className="mt-4 border-t pt-3 text-[12px] leading-relaxed" style={{ color: "var(--text-2)", borderColor: "var(--line)" }}>{bp.rationale}</p>}
        </section>
      </div>
    </div>
  );
}

/* ---------------- hooks ---------------- */
export function MetaAdMock({ brand, copy, image }: { brand: string; copy: Json; image?: string | null }) {
  return (
    <div className="overflow-hidden rounded-2xl border bg-white text-[12px]" style={{ borderColor: "var(--line)" }}>
      <div className="flex items-center gap-2 p-2.5">
        <span className="grid h-7 w-7 place-items-center rounded-full text-[10px] font-bold text-white" style={{ background: "var(--primary)" }}>{brand.slice(0, 2).toUpperCase()}</span>
        <div><div className="font-semibold">{brand}</div><div className="meta">Sponsored</div></div>
      </div>
      <p className="px-2.5 pb-2 leading-snug">{copy.primary_text}</p>
      {image ? <img src={image} alt="" className="aspect-[4/5] w-full object-cover" /> : <div className="aspect-[4/3] w-full" style={{ background: "linear-gradient(135deg,#dfe6f0,#aebfd6)" }} />}
      <div className="flex items-center justify-between gap-2 p-2.5" style={{ background: "#f4f6f9" }}>
        <div className="min-w-0"><div className="truncate font-semibold">{copy.headline}</div><div className="meta truncate">{copy.description}</div></div>
        <span className="rounded-md px-2.5 py-1.5 font-semibold" style={{ background: "#e4e7ec" }}>{cta(copy.cta)}</span>
      </div>
    </div>
  );
}

export function HooksView({ hooks, bp, brand }: { hooks: Json[] | null; bp: Json | null; brand: string }) {
  const [pick, setPick] = useState<string | null>(null);
  if (!hooks?.length) return <Empty text="The hook writer drafts ~10 hooks per ad set; the critic keeps the best." />;
  const adsets: Json[] = bp?.campaigns?.flatMap((c: Json) => c.adsets) ?? [];
  const sel = hooks.find((h) => h.id === pick) ?? hooks[0];
  return (
    <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr] fade-in">
      <div className="flex flex-col gap-4">
        {(adsets.length ? adsets : [{ ref: null, name: "Hooks" }]).map((s: Json) => (
          <section key={s.ref ?? "all"} className="card">
            <div className="flex items-center justify-between"><div className="title">{s.name}</div><span className="meta">{s.angle}</span></div>
            <div className="mt-2 flex flex-col">
              {hooks.filter((h) => !s.ref || h.adset_ref === s.ref).map((h) => (
                <button key={h.id} onClick={() => setPick(h.id)} className={`row text-left rounded-xl px-2 ${sel?.id === h.id ? "bg-white" : ""}`}>
                  <div className="w-11 shrink-0 text-right">
                    <div className="card-num !text-[18px]">{h.critic_score ?? "–"}</div>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] font-medium leading-snug">{h.text}</div>
                    <div className="mt-1.5 h-1 rounded-full" style={{ background: "var(--bar)" }}>
                      <div className="h-1 rounded-full" style={{ width: `${(h.critic_score ?? 5) * 10}%`, background: (h.critic_score ?? 0) >= 8 ? "var(--accent)" : "var(--ink)" }} />
                    </div>
                  </div>
                  <span className="pill shrink-0">{h.framework || "hook"}</span>
                  {h.id.includes("_x") ? <span className="pill shrink-0">reserve</span> : <Star size={14} className="shrink-0" style={{ color: "var(--warn)" }} />}
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>
      <section className="card self-start lg:sticky lg:top-6">
        <div className="label flex items-center gap-1.5"><Sparkles size={12} /> Ad preview · {sel?.id}</div>
        <div className="mt-3">{sel?.ad_copy ? <MetaAdMock brand={brand} copy={sel.ad_copy} /> : <div className="meta">No copy for this hook.</div>}</div>
        {sel?.scene && <div className="tile mt-3 text-[12px]"><span className="label">Scene brief · </span>{sel.scene}</div>}
        <div className="mt-3 flex gap-2"><span className="pill ok"><CheckCircle2 size={11} /> policy pre-check passed</span><span className="pill">≤40 headline</span></div>
      </section>
    </div>
  );
}

/* ---------------- creatives ---------------- */
export function PhoneFrame({ c, width = 220 }: { c: Json; width?: number }) {
  const [w, h] = [c.width, c.height];
  const src = fileUrl(c.path);
  return (
    <div className="phone" style={{ width }}>
      <div className="screen" style={{ aspectRatio: c.ratio === "9:16" ? "9/16" : "9/16" }}>
        <div className="notch" />
        <div className="absolute inset-0 grid place-items-center" style={{ background: "#000" }}>
          {c.kind === "video"
            ? <video src={src} poster={c.thumbnail_path ? fileUrl(c.thumbnail_path) : undefined} className="w-full" style={{ aspectRatio: `${w}/${h}` }} controls muted loop playsInline />
            : <img src={src} alt={c.creative_id} className="w-full" style={{ aspectRatio: `${w}/${h}` }} />}
        </div>
      </div>
    </div>
  );
}

export function QAChip({ qa }: { qa: Json }) {
  return qa?.passed
    ? <span className="pill ok"><CheckCircle2 size={11} /> QA {Math.round(qa.score)}</span>
    : <span className="pill bad"><XCircle size={11} /> QA {Math.round(qa?.score ?? 0)}</span>;
}

/* ---------------- audit ---------------- */
export function ScoreRing({ value, label, size = 88 }: { value: number; label: string; size?: number }) {
  const r = size / 2 - 7, c = 2 * Math.PI * r;
  const col = value >= 85 ? "var(--accent)" : value >= 70 ? "var(--primary)" : "var(--danger)";
  return (
    <div className="flex flex-col items-center gap-1.5">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--bar)" strokeWidth={8} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke={col} strokeWidth={8} fill="none" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - value / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle" fontSize={size / 4.2} fontWeight={600} fill="var(--text)">{value}</text>
      </svg>
      <span className="label capitalize">{label.replace("_", " ")}</span>
    </div>
  );
}

export function AuditSummary({ a, round }: { a: Json | null; round?: number }) {
  if (!a) return <Empty text="The Auditor checks brief, creatives, copy policy, budget, targeting and the Meta build." />;
  const tone = a.decision === "FAIL" ? "bad" : a.decision === "PASS" ? "ok" : "warn";
  return (
    <section className="card fade-in">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="label">Auditor verdict{round ? ` · round ${round}` : ""}</div>
          <div className="page-title mt-1">{a.decision.replace(/_/g, " ").toLowerCase().replace(/^./, (x: string) => x.toUpperCase())}</div>
        </div>
        <span className={`pill ${tone} !text-[12px] !px-3 !py-1.5`}>{a.findings?.length ?? 0} findings</span>
      </div>
      <div className="mt-5 grid grid-cols-3 gap-4 sm:grid-cols-4 lg:grid-cols-7">
        {Object.entries(a.scores ?? {}).map(([k, v]) => <ScoreRing key={k} label={k} value={v as number} size={78} />)}
      </div>
    </section>
  );
}

export const UP = ArrowUpRight;
