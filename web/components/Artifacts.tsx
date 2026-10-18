"use client";
import { fileUrl, money, type Json } from "@/lib/api";
import { ScoreRing } from "@/components/rune/Views";

/** Renders whatever a gate is asking the human to approve. Unknown shapes fall back to JSON. */
export default function Artifact({ artifact, brief }: { artifact: Json; brief?: Json }) {
  if (!artifact) return null;
  const cur = brief?.budget?.currency ?? "INR";
  return (
    <div className="flex flex-col gap-4">
      {Object.entries(artifact).map(([key, value]) => {
        if (value == null) return null;
        const View = VIEWS[key];
        return <div key={key}>{View ? <View v={value} cur={cur} /> : <Raw v={value} />}</div>;
      })}
    </div>
  );
}

const VIEWS: Record<string, (p: { v: Json; cur: string }) => React.ReactNode> = {
  brief: ({ v }) => (
    <div className="grid gap-2 sm:grid-cols-2">
      <Field k="Business" val={`${v.business_name} · ${v.industry}`} />
      <Field k="Goal" val={`${v.goal} · ${v.geo?.join(", ")}`} />
      <Field k="Budget" val={`${money(v.budget?.total, v.budget?.currency)} · ${v.budget?.start} → ${v.budget?.end}`} />
      <Field k="Deliverables" val={`${v.deliverables?.n_images} images, ${v.deliverables?.n_videos} videos · ${v.deliverables?.ratios?.join(" ")}`} />
      <Field k="Products" val={v.products?.map((p: Json) => p.name).join(", ")} />
      <Field k="Target CPA" val={v.kpi_targets?.target_cpa ? money(v.kpi_targets.target_cpa, v.budget?.currency) : "not set"} />
    </div>
  ),
  research: ({ v }) => (
    <div className="flex flex-col gap-3">
      <p className="text-[13px]">{v.summary}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {v.personas?.map((p: Json) => (
          <div key={p.ref} className="tile">
            <div className="title">{p.name}</div>
            <div className="label mt-1">{p.description}</div>
            {p.phrases?.length > 0 && <div className="meta mt-2 italic">“{p.phrases[0]}”</div>}
          </div>
        ))}
      </div>
      {v.recommended_angles?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">{v.recommended_angles.map((a: string) => <span key={a} className="pill blue">{a}</span>)}</div>
      )}
    </div>
  ),
  blueprint: ({ v }) => (
    <div className="flex flex-col gap-2">
      {v.campaigns?.map((c: Json) => (
        <div key={c.ref} className="tile">
          <div className="flex items-center gap-2"><span className="title">{c.name}</span><span className="pill blue">{c.funnel_stage}</span><span className="pill">{c.objective}</span></div>
          {c.adsets?.map((s: Json) => (
            <div key={s.ref} className="mt-2 ml-3 border-l pl-3" style={{ borderColor: "var(--line)" }}>
              <div className="text-[13px]">{s.name} <span className="meta">· {s.angle}</span></div>
              <div className="meta">{s.ads?.length} ads · {s.ads?.map((a: Json) => a.format).join(", ")}</div>
            </div>
          ))}
        </div>
      ))}
      {v.rationale && <p className="label">{v.rationale}</p>}
    </div>
  ),
  hooks: ({ v }) => (
    <div className="flex flex-col">
      {v.map((h: Json) => (
        <div key={h.id} className="row">
          <span className="pill">{h.framework || "hook"}</span>
          <span className="flex-1 text-[13px]">{h.text}</span>
          {h.critic_score != null && <span className={`pill ${h.critic_score >= 8 ? "ok" : ""}`}>{h.critic_score}/10</span>}
        </div>
      ))}
    </div>
  ),
  package: ({ v }) => <CreativeGrid creatives={v.creatives ?? []} />,
  budget_plan: ({ v, cur }) => {
    const max = Math.max(1, ...Object.values<number>(v.adset_daily));
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          <span className="pill blue">{v.mode}</span>
          <span className="pill">{v.bid_strategy}</span>
          <span className="pill">{v.days} days</span>
          <span className="pill ok">reserve {money(v.testing_reserve, cur)}/day</span>
          {v.target_cpa && <span className="pill">target CPA {money(v.target_cpa, cur)}</span>}
        </div>
        {Object.entries<number>(v.adset_daily).map(([ref, amt]) => (
          <div key={ref}>
            <div className="flex justify-between text-[13px]"><span>{ref}</span><span className="font-medium">{money(amt, cur)}/day</span></div>
            <div className="mt-1 h-2 rounded-full" style={{ background: "var(--bar)" }}>
              <div className="h-2 rounded-full" style={{ width: `${(amt / max) * 100}%`, background: "var(--primary)" }} />
            </div>
          </div>
        ))}
        <p className="label leading-relaxed">{v.rationale}</p>
      </div>
    );
  },
  targeting: ({ v }) => (
    <div className="flex flex-col">
      {v.map((t: Json) => (
        <div key={t.adset_ref} className="row">
          <span className="pill blue">{t.adset_ref}</span>
          <span className="flex-1 text-[13px]">{t.summary}</span>
        </div>
      ))}
    </div>
  ),
  audit: ({ v }) => <AuditView v={v} />,
};

export function AuditView({ v }: { v: Json }) {
  const tone = v.decision === "PASS" ? "ok" : v.decision === "FAIL" ? "bad" : "warn";
  return (
    <div className="flex flex-col gap-3">
      <div><span className={`pill ${tone}`}>{v.decision}</span></div>
      <div className="grid grid-cols-3 gap-4 sm:grid-cols-5 lg:grid-cols-7">
        {Object.entries<number>(v.scores ?? {}).map(([k, s]) => <ScoreRing key={k} label={k} value={s} size={78} />)}
      </div>
      {v.findings?.map((f: Json, i: number) => (
        <div key={i} className="row">
          <span className={`pill ${f.severity === "blocker" ? "bad" : f.severity === "major" ? "warn" : ""}`}>{f.severity}</span>
          <span className="flex-1 text-[13px]">{f.message}</span>
          <span className="meta">{f.owner} · {f.object_ref}</span>
        </div>
      ))}
    </div>
  );
}

export function CreativeGrid({ creatives }: { creatives: Json[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {creatives.map((c) => (
        <figure key={c.creative_id} className="tile !p-2">
          <div className="overflow-hidden rounded-[10px]" style={{ aspectRatio: `${c.width}/${c.height}`, background: "var(--bar)" }}>
            {c.kind === "image" || c.thumbnail_path ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={fileUrl(c.kind === "image" ? c.path : c.thumbnail_path)} alt={c.ad_copy?.headline} className="h-full w-full object-cover" />
            ) : (
              <video src={fileUrl(c.path)} className="h-full w-full object-cover" muted />
            )}
          </div>
          <figcaption className="mt-2">
            <div className="text-[12px] font-medium leading-snug">{c.ad_copy?.headline}</div>
            <div className="mt-1 flex items-center gap-1.5">
              <span className="pill">{c.ratio}</span>
              <span className="pill">{c.kind}</span>
              <span className={`pill ${c.qa?.passed ? "ok" : "bad"}`}>QA {Math.round(c.qa?.score ?? 0)}</span>
            </div>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function Field({ k, val }: { k: string; val: React.ReactNode }) {
  return (
    <div className="tile !py-2.5">
      <div className="label">{k}</div>
      <div className="text-[13px] mt-0.5">{val ?? "—"}</div>
    </div>
  );
}

function Raw({ v }: { v: Json }) {
  return <pre className="tile max-h-64 overflow-auto text-[11px] leading-relaxed">{JSON.stringify(v, null, 2)}</pre>;
}
