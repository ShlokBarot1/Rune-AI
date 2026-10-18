"use client";
import { useState } from "react";
import { Film, ImageIcon, Layers, Pencil, Send, Target, Users, Wallet } from "lucide-react";
import { fileUrl, money, type Json } from "@/lib/api";
import { changeStage, reopenNote } from "@/lib/run";

const sum = (o: Record<string, number> = {}) => Object.values(o).reduce((a, b) => a + b, 0);
const adsetsOf = (bp: Json): Json[] => bp?.campaigns?.flatMap((c: Json) => c.adsets) ?? [];
const nameOf = (bp: Json, ref: string) => adsetsOf(bp).find((s) => s.ref === ref)?.name ?? ref;
const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
const Empty = ({ text }: { text: string }) => <div className="tile py-10 text-center text-[13px]" style={{ color: "var(--text-2)" }}>{text}</div>;

function Big({ label, value, sub, dark }: { label: string; value: React.ReactNode; sub?: string; dark?: boolean }) {
  return (
    <div className={`rounded-[20px] p-4 ${dark ? "text-white" : "tile"}`} style={dark ? { background: "var(--ink)" } : undefined}>
      <div className="label" style={dark ? { color: "#9aa3b2" } : undefined}>{label}</div>
      <div className="card-num mt-1">{value}</div>
      {sub && <div className="meta mt-0.5" style={dark ? { color: "#9aa3b2" } : undefined}>{sub}</div>}
    </div>
  );
}

/* ---------------- budget ---------------- */
export function BudgetView({ v }: { v: Json }) {
  const bp = v.budget_plan, cur = bp?.currency ?? v.brief?.budget?.currency ?? "INR";
  if (!bp) return <Empty text="The budget agent splits the money once creatives are approved." />;
  const adsetDaily = sum(bp.adset_daily), daily = adsetDaily + (bp.testing_reserve ?? 0);
  const flight = daily * bp.days;
  return (
    <section className="card fade-in flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div><div className="label flex items-center gap-1.5"><Wallet size={12} /> Budget plan</div><div className="text-[18px] font-semibold">How {money(bp.total, cur)} gets spent</div></div>
        <div className="flex gap-1.5"><span className="pill dark">{bp.mode}</span><span className="pill">{bp.bid_strategy.replace(/_/g, " ").toLowerCase()}</span></div>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Big dark label="Total budget" value={money(bp.total, cur)} sub={bp.net_total ? `${money(bp.net_total, cur)} after GST` : `${bp.days} days`} />
        <Big label="Daily spend" value={money(daily, cur)} sub={`× ${bp.days} days = ${money(flight, cur)}`} />
        <Big label="Ad sets / day" value={money(adsetDaily, cur)} sub={`${pct(adsetDaily, daily)}% of daily`} />
        <Big label="Testing reserve / day" value={money(bp.testing_reserve, cur)} sub="held for new creatives from live learnings" />
      </div>
      <div>
        <div className="label mb-2">Daily split</div>
        <div className="flex h-3 overflow-hidden rounded-full" style={{ background: "var(--bar)" }}>
          {Object.entries<number>(bp.adset_daily).map(([k, d], i) => (
            <div key={k} title={`${nameOf(v.blueprint, k)} ${money(d, cur)}`} style={{ width: `${pct(d, daily)}%`, background: ["var(--ink)", "var(--primary)", "#5b7bb5", "#8fa6cf", "#b8c6df"][i % 5] }} />
          ))}
          <div title="Testing reserve" style={{ width: `${pct(bp.testing_reserve, daily)}%`, background: "var(--accent)" }} />
        </div>
      </div>
      <table className="data">
        <thead><tr><th>Ad set</th><th className="text-right">Daily</th><th className="text-right">Share</th><th className="text-right">Over {bp.days} days</th></tr></thead>
        <tbody>
          {Object.entries<number>(bp.adset_daily).map(([k, d]) => (
            <tr key={k}><td><b>{nameOf(v.blueprint, k)}</b><div className="meta">{k}</div></td><td className="text-right">{money(d, cur)}</td><td className="text-right">{pct(d, daily)}%</td><td className="text-right">{money(d * bp.days, cur)}</td></tr>
          ))}
          <tr><td><b>Testing reserve</b><div className="meta">new variants from the feedback loop</div></td><td className="text-right">{money(bp.testing_reserve, cur)}</td><td className="text-right">{pct(bp.testing_reserve, daily)}%</td><td className="text-right">{money(bp.testing_reserve * bp.days, cur)}</td></tr>
          {Object.entries<number>(bp.campaign_daily ?? {}).map(([k, d]) => (
            <tr key={k} style={{ background: "var(--tile)" }}><td><b>Campaign</b> {k}</td><td className="text-right">{money(d, cur)}</td><td /><td className="text-right">{money(d * bp.days, cur)}</td></tr>
          ))}
        </tbody>
      </table>
      {bp.rationale && <p className="text-[12.5px] leading-relaxed" style={{ color: "var(--text-2)" }}>{bp.rationale}</p>}
    </section>
  );
}

/* ---------------- targeting ---------------- */
type Draft = { age_min: number; age_max: number; genders: string; countries: string; interests: string; placements: string };
function draftOf(t: Json, persona?: Json): Draft {
  const s = t.spec ?? {};
  const resolved = s.flexible_spec?.[0]?.interests?.map((i: Json) => i.name) ?? [];
  const fromSummary = (t.summary?.match(/interests(?: to resolve)?: ([^.]+)/)?.[1] ?? "").split(",").map((x: string) => x.trim()).filter(Boolean);
  return {
    age_min: s.age_min ?? 18, age_max: s.age_max ?? 65,
    genders: !s.genders ? "all" : s.genders[0] === 1 ? "men" : "women",
    countries: (s.geo_locations?.countries ?? []).join(", "),
    interests: (resolved.length ? resolved : fromSummary.length ? fromSummary : persona?.interests ?? []).join(", "),
    placements: s.publisher_platforms ? "Feeds only" : "Advantage+",
  };
}

export function TargetingView({ v, cid, editable, atGate, onSent }: { v: Json; cid: string | null; editable: boolean; atGate: boolean; onSent: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (!v.targeting) return <Empty text="The targeting agent turns each persona into a Meta audience." />;
  const personas = Object.fromEntries((v.research?.personas ?? []).map((p: Json) => [p.ref, p]));
  const changed = Object.keys(drafts);
  const send = async () => {
    if (!cid) return;
    const lines = changed.map((ref) => {
      const d = drafts[ref];
      return `Ad set ${ref}: age ${d.age_min}-${d.age_max}; genders ${d.genders}; countries ${d.countries}; interests exactly: ${d.interests}; placements ${d.placements}.`;
    });
    setBusy(true);
    try { await changeStage(cid, "targeting", `Apply these manual targeting edits exactly:\n${lines.join("\n")}`); setMsg("Sent to the targeting agent — it resolves interests to Meta IDs and rebuilds the specs."); setDrafts({}); onSent(); }
    catch (e) { setMsg(String((e as Error).message)); } finally { setBusy(false); }
  };

  return (
    <div className="flex flex-col gap-4 fade-in">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className="label flex items-center gap-1.5"><Target size={12} /> Targeting & placements</div><div className="text-[18px] font-semibold">AI-built audiences, one per ad set</div></div>
        {changed.length > 0 && <button className="btn btn-lime" disabled={busy || !editable} onClick={send}><Send size={14} /> Apply {changed.length} edit{changed.length > 1 ? "s" : ""}</button>}
      </div>
      {msg && <div className="pill blue !whitespace-normal !text-[12px]">{msg}</div>}
      <div className="grid gap-4 lg:grid-cols-2">
        {v.targeting.map((t: Json) => {
          const s = adsetsOf(v.blueprint).find((a) => a.ref === t.adset_ref);
          const base = draftOf(t, personas[s?.persona_ref]);
          const d = drafts[t.adset_ref] ?? base;
          const set = (k: keyof Draft, val: string | number) => setDrafts({ ...drafts, [t.adset_ref]: { ...d, [k]: val } });
          return (
            <section key={t.adset_ref} className="card flex flex-col gap-3">
              <div className="flex items-start justify-between gap-2">
                <div><div className="text-[15px] font-semibold">{s?.name ?? t.adset_ref}</div><div className="meta">{personas[s?.persona_ref]?.name ?? s?.persona_ref}</div></div>
                {editable && <button className="btn btn-ghost !h-8 !text-[12px]" onClick={() => setEdit(edit === t.adset_ref ? null : t.adset_ref)}><Pencil size={12} /> {edit === t.adset_ref ? "Done" : "Edit"}</button>}
              </div>
              {edit === t.adset_ref ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="flex flex-col gap-1"><span className="label">Age from</span><input className="input" type="number" min={18} max={65} value={d.age_min} onChange={(e) => set("age_min", Number(e.target.value))} /></label>
                  <label className="flex flex-col gap-1"><span className="label">Age to</span><input className="input" type="number" min={18} max={65} value={d.age_max} onChange={(e) => set("age_max", Number(e.target.value))} /></label>
                  <label className="flex flex-col gap-1"><span className="label">Gender</span><select className="input" value={d.genders} onChange={(e) => set("genders", e.target.value)}>{["all", "men", "women"].map((g) => <option key={g}>{g}</option>)}</select></label>
                  <label className="flex flex-col gap-1"><span className="label">Countries</span><input className="input" value={d.countries} onChange={(e) => set("countries", e.target.value.toUpperCase())} /></label>
                  <label className="flex flex-col gap-1 sm:col-span-2"><span className="label">Interests (comma separated)</span><textarea className="input min-h-14" value={d.interests} onChange={(e) => set("interests", e.target.value)} /></label>
                  <label className="flex flex-col gap-1"><span className="label">Placements</span><select className="input" value={d.placements} onChange={(e) => set("placements", e.target.value)}>{["Advantage+", "Feeds only"].map((g) => <option key={g}>{g}</option>)}</select></label>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <div className="flex flex-wrap gap-1.5">
                    <span className="pill dark"><Users size={11} /> {d.genders} · {d.age_min}–{d.age_max}</span>
                    <span className="pill">{d.countries}</span><span className="pill">{d.placements}</span>
                    {drafts[t.adset_ref] && <span className="pill warn">edited — not applied yet</span>}
                  </div>
                  <div className="flex flex-wrap gap-1.5">{d.interests.split(",").filter(Boolean).map((i) => <span key={i} className="pill blue">{i.trim()}</span>)}</div>
                  <p className="meta leading-relaxed">{t.summary}</p>
                </div>
              )}
            </section>
          );
        })}
      </div>
      {!editable ? <div className="meta">Editing opens whenever the run is waiting for you (not while agents work or once ads are live).</div>
        : changed.length > 0 && !atGate && <div className="meta">{reopenNote("Targeting", false)} Only the Meta build and audit rerun; creatives stay.</div>}
    </div>
  );
}

/* ---------------- Meta build + expectations ---------------- */
const BENCH: Record<string, { cpm: number }> = { INR: { cpm: 140 }, USD: { cpm: 9 }, EUR: { cpm: 8 }, GBP: { cpm: 8 } };
const CVR: Record<string, number> = { sales: 0.022, leads: 0.07, traffic: 0, awareness: 0, engagement: 0, app_installs: 0.05 };

export function MetaBuildView({ v }: { v: Json }) {
  const L = v.launch, bp = v.budget_plan, pkg = v.package, cur = bp?.currency ?? v.brief?.budget?.currency ?? "INR";
  if (!L) return <Empty text="The launch agent builds everything on Meta, PAUSED, for the final audit." />;
  const status = (ref: string) => L.objects.find((o: Json) => o.local_ref === ref)?.status;
  const tgt = Object.fromEntries((v.targeting ?? []).map((t: Json) => [t.adset_ref, t]));
  const counts = ["campaign", "adset", "ad", "image", "video"].map((k) => [k, L.objects.filter((o: Json) => o.kind === k).length] as const);

  const daily = bp ? sum(bp.adset_daily) : 0, days = bp?.days ?? 30, goal = v.brief?.goal ?? "sales";
  const cpm = (BENCH[cur] ?? BENCH.USD).cpm, ctr = 0.012, cvr = CVR[goal] ?? 0.02;
  const imp = (daily / cpm) * 1000, clicks = imp * ctr, conv = clicks * cvr;
  const target = bp?.target_cpa ?? v.brief?.kpi_targets?.target_cpa;
  const estCpa = conv ? daily / conv : null;

  return (
    <div className="flex flex-col gap-4 fade-in">
      <section className="card">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div><div className="label flex items-center gap-1.5"><Layers size={12} /> Meta build {L.dry_run ? "· dry run" : "· sandbox"}</div><div className="text-[18px] font-semibold">Everything created PAUSED until you go live</div></div>
          <div className="flex flex-wrap gap-1.5">{counts.map(([k, n]) => <span key={k} className="pill">{n} {k}{n === 1 ? "" : "s"}</span>)}</div>
        </div>
        {v.blueprint?.campaigns?.map((c: Json) => (
          <div key={c.ref} className="mt-4">
            <div className="flex flex-wrap items-center gap-2 rounded-[18px] p-3 text-white" style={{ background: "var(--ink)" }}>
              <span className="font-semibold">{c.name}</span><span className="pill lime">{c.objective}</span><span className="pill dark">{c.funnel_stage}</span>
              {bp?.campaign_daily?.[c.ref] != null && <span className="pill dark">{money(bp.campaign_daily[c.ref], cur)}/day</span>}
              <span className="ml-auto pill dark">{status(`campaign:${c.ref}`) ?? "PAUSED"}</span>
            </div>
            <div className="mt-3 flex flex-col gap-3 border-l-2 pl-4" style={{ borderColor: "var(--line)" }}>
              {c.adsets.map((s: Json) => {
                const creatives = (pkg?.creatives ?? []).filter((x: Json) => x.adset_ref === s.ref);
                return (
                  <div key={s.ref} className="tile flex flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <b className="text-[14px]">{s.name}</b>
                      {bp?.adset_daily?.[s.ref] != null && <span className="pill dark">{money(bp.adset_daily[s.ref], cur)}/day</span>}
                      <span className="pill">{status(`adset:${s.ref}`) ?? "PAUSED"}</span>
                    </div>
                    {tgt[s.ref] && <div className="meta">{tgt[s.ref].summary}</div>}
                    <div className="flex flex-wrap gap-2">
                      {creatives.map((x: Json) => (
                        <div key={x.creative_id} className="flex items-center gap-2 rounded-xl bg-white p-1.5 pr-3" title={x.ad_copy.primary_text}>
                          <img src={fileUrl(x.kind === "video" && x.thumbnail_path ? x.thumbnail_path : x.path)} alt="" className="h-10 w-8 rounded-md object-cover" />
                          <div className="leading-tight">
                            <div className="flex items-center gap-1 text-[11.5px] font-semibold">{x.kind === "video" ? <Film size={10} /> : <ImageIcon size={10} />}{x.ratio}</div>
                            <div className="meta max-w-[150px] truncate">{x.ad_copy.headline}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </section>

      {bp && (
        <section className="card">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div><div className="label">Expected results</div><div className="text-[18px] font-semibold">What this budget should deliver</div></div>
            <span className="meta max-w-sm text-right">Category benchmark estimates (CPM {money(cpm, cur)}, CTR {(ctr * 100).toFixed(1)}%{cvr ? `, conversion ${(cvr * 100).toFixed(1)}%` : ""}). Live data replaces them.</span>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Big label="Impressions / day" value={Math.round(imp).toLocaleString()} sub={`${Math.round(imp * days).toLocaleString()} total`} />
            <Big label="Clicks / day" value={Math.round(clicks).toLocaleString()} sub={`${Math.round(clicks * days).toLocaleString()} total`} />
            <Big label="CTR" value={`${(ctr * 100).toFixed(1)}%`} sub="feed benchmark" />
            <Big label="CPC" value={clicks ? money(daily / clicks, cur) : "—"} />
            <Big label={goal === "leads" ? "Leads" : "Conversions"} value={cvr ? Math.round(conv * days).toLocaleString() : "—"} sub={cvr ? `${conv.toFixed(1)} / day` : "not a conversion goal"} />
            <Big dark label="Est. CPA" value={estCpa ? money(estCpa, cur) : "—"} sub={target ? `target ${money(target, cur)} · ${estCpa && estCpa <= target ? "on track" : "above target"}` : "no target set"} />
          </div>
        </section>
      )}
    </div>
  );
}
