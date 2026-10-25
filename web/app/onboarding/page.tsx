"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Film, Globe, ImagePlus, Sparkles, Wand2, X } from "lucide-react";
import { msgText, post, usePoll, useEvents, type Json } from "@/lib/api";
import { BriefCard } from "@/components/rune/Views";
import ApprovalDock from "@/components/rune/ApprovalDock";
import BriefEditor from "@/components/rune/BriefEditor";
import { uploadFile } from "@/lib/run";

type Own = { file: File; url: string; primary_text: string; headline: string };

/** The client's finished ads: pick files, optionally type their copy. Nothing gets generated for these. */
function OwnCreatives({ items, setItems }: { items: Own[]; setItems: (f: (x: Own[]) => Own[]) => void }) {
  const add = (files: FileList | null) => {
    // copy now: the FileList empties when the input is reset, before React runs the updater
    const picked = Array.from(files ?? []).map((file) => ({ file, url: URL.createObjectURL(file), primary_text: "", headline: "" }));
    setItems((x) => [...x, ...picked]);
  };
  const set = (i: number, k: "primary_text" | "headline", v: string) => setItems((x) => x.map((o, j) => (j === i ? { ...o, [k]: v } : o)));
  return (
    <div className="mt-3 flex flex-col gap-2">
      {items.map((o, i) => (
        <div key={o.url} className="tile flex items-start gap-3 !p-2.5">
          <div className="relative h-20 w-16 shrink-0 overflow-hidden rounded-xl bg-black">
            {o.file.type.startsWith("video")
              ? <><video src={o.url} muted className="h-full w-full object-cover" /><Film size={12} className="absolute right-1 top-1 text-white" /></>
              : <img src={o.url} alt="" className="h-full w-full object-cover" />}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <div className="meta truncate">{o.file.name} · {(o.file.size / 1e6).toFixed(1)} MB</div>
            <input className="input !py-1.5 !text-[13px]" placeholder="Headline (optional, the agents write it otherwise)" maxLength={60}
              value={o.headline} onChange={(e) => set(i, "headline", e.target.value)} />
            <input className="input !py-1.5 !text-[13px]" placeholder="Primary text (optional)" maxLength={500}
              value={o.primary_text} onChange={(e) => set(i, "primary_text", e.target.value)} />
          </div>
          <button className="circle sm" aria-label={`Remove ${o.file.name}`} onClick={() => { URL.revokeObjectURL(o.url); setItems((x) => x.filter((_, j) => j !== i)); }}><X size={13} /></button>
        </div>
      ))}
      <label className="tile flex cursor-pointer items-center justify-center gap-2 !py-4 text-[13px] font-semibold" style={{ borderStyle: "dashed" }}>
        <ImagePlus size={16} /> Add images or videos (PNG, JPG, WEBP, MP4, MOV)
        <input type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v" className="hidden"
          onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </label>
    </div>
  );
}

const EXAMPLES = ["https://sleepyowl.co", "https://www.bluetokaicoffee.com", "https://www.thewholetruthfoods.com"];

/** Intake's live draft (from its bus log) -> the ClientBrief shape the brief card renders. */
function draftToBrief(d: Json, website: string): Json {
  return {
    business_name: d.business_name, industry: d.industry, website, products: d.products, brand: d.brand, goal: d.goal,
    geo: d.geo, kpi_targets: { target_cpa: d.target_cpa },
    budget: { total: d.budget_total, currency: d.currency ?? "INR", start: d.start, end: d.end },
    deliverables: d.n_images != null && d.n_videos != null ? { n_images: d.n_images, n_videos: d.n_videos } : null,
  };
}

export default function Onboarding() {
  const router = useRouter();
  const [cid, setCid] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<"ai" | "own">("ai");
  const [own, setOwn] = useState<Own[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setCid(new URLSearchParams(window.location.search).get("client")); }, []);

  const [snap, , reload] = usePoll<Json>(cid ? `/runs/${cid}` : null, 2000);
  const msgs = useEvents(cid, 300);
  const end = useRef<HTMLDivElement>(null);
  const chat = msgs.filter((m) => m.sender === "malav.intake" || (m.sender === "human" && m.recipient === "malav.intake"));
  useEffect(() => { const b = end.current?.parentElement; b?.scrollTo({ top: b.scrollHeight, behavior: "smooth" }); }, [chat.length]);

  const liveBrief = useMemo(() => {
    if (snap?.values?.brief) return snap.values.brief;
    let d: Json = null;
    for (const m of msgs) {
      if (m.sender !== "malav.intake") continue;
      if (m.payload?.draft) d = { ...m.payload.draft };
      if (d && m.payload?.patch) d = { ...d, ...m.payload.patch };
    }
    return d ? draftToBrief(d, url || snap?.values?.intake?.website) : null;
  }, [msgs, snap, url]);

  const pending = snap?.pending?.[0];
  const intakeDone = !!snap?.values?.brief && !(pending?.type === "gate" && pending?.gate === "intake") && !snap?.next?.[0]?.startsWith("intake");

  const ready = !!url.trim() && (mode === "ai" || own.length > 0);
  const start = async () => {
    setBusy(true); setErr(null);
    try {
      const id = `client_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
      const creatives = [];
      for (const o of own.filter(() => mode === "own")) {  // one at a time: videos can be large
        creatives.push({ path: await uploadFile(id, o.file), primary_text: o.primary_text.trim(), headline: o.headline.trim() });
      }
      const r = await post("/runs", { client_id: id, intake: { website: url.trim(), notes, ...(creatives.length ? { creatives } : {}) } });
      setCid(r.client_id);
      window.history.replaceState(null, "", `/onboarding?client=${r.client_id}`);
    } catch (e) { setErr(String((e as Error).message ?? e)); } finally { setBusy(false); }
  };

  if (!cid) return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 pt-6 sm:pt-14">
      <div className="text-center">
        <span className="pill lime !text-[12px] !px-3 !py-1"><Sparkles size={12} /> 14 agents · one URL</span>
        <h1 className="page-title mt-4 !text-[44px] sm:!text-[54px]">Launch a brand on Meta,<br />start to finish.</h1>
        <p className="mx-auto mt-3 max-w-lg text-[15px]" style={{ color: "var(--text-2)" }}>
          Paste a website. The agents read it, research the market, plan the campaign, write hooks and produce every ad — and ask you only what they can&apos;t find.
        </p>
      </div>
      <section className="card !p-3 sm:!p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <Globe size={16} className="absolute left-4 top-1/2 -translate-y-1/2" style={{ color: "var(--text-3)" }} />
            <input className="input !h-14 !rounded-full !pl-11 !text-[15px]" placeholder="https://yourbrand.com" value={url}
              onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ready && !busy && start()} />
          </div>
          <button className="btn btn-primary !h-14 !px-7 !text-[14px]" disabled={!ready || busy} onClick={start}>
            <Wand2 size={16} /> {busy ? (mode === "own" ? "Uploading…" : "Starting…") : "Start agents"}
          </button>
        </div>
        <textarea className="input mt-2 !rounded-[20px] min-h-16" placeholder="Optional: anything we should know — budget, dates, goal, markets…" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="label">Ad creatives</span>
          <div className="seg">
            <button className={mode === "ai" ? "on" : ""} onClick={() => setMode("ai")}>Agents make them</button>
            <button className={mode === "own" ? "on" : ""} onClick={() => setMode("own")}>I have my own</button>
          </div>
          <span className="meta">{mode === "own" ? "Your files run as they are. No AI images or video." : "AI backgrounds, your real product photo, rendered copy."}</span>
        </div>
        {mode === "own" && <OwnCreatives items={own} setItems={setOwn} />}
        {err && <div className="pill bad mt-2 !whitespace-normal">{err}</div>}
      </section>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((e) => <button key={e} className="pill !py-1.5 !px-3 !text-[12px]" style={{ background: "#fff" }} onClick={() => setUrl(e)}>{e.replace("https://", "")}</button>)}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="label">New campaign · intake</div>
          <h1 className="page-title mt-1">{liveBrief?.business_name || "Reading the website…"}</h1>
        </div>
        {intakeDone && <button className="btn btn-lime" onClick={() => router.push(`/run?client=${cid}`)}>Continue in Mission Control <ArrowRight size={15} /></button>}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <section className="card flex flex-col" style={{ height: "min(72vh, 760px)" }}>
          <div className="flex items-center gap-2 pb-3">
            <span className="grid h-9 w-9 place-items-center rounded-full" style={{ background: "var(--ink)", color: "var(--accent)" }}><Sparkles size={15} /></span>
            <div><div className="title">Intake agent</div><div className="meta">reads the site, then asks only what&apos;s missing</div></div>
          </div>
          <div className="-mr-2 flex flex-1 flex-col gap-2 overflow-y-auto pl-1 pr-2">{/* pl-1: room for the dots' 2px outline */}
            {chat.map((m) => m.sender === "human"
              ? <div key={m.seq} className="bubble light-right fade-in">{String(m.payload?.answer ?? msgText(m))}</div>
              : m.type === "QUESTION"
                ? <div key={m.seq} className="bubble light-left fade-in !font-semibold">{m.payload.question}</div>
                : <div key={m.seq} className="fade-in flex items-start gap-2 text-[12.5px]" style={{ color: "var(--text-2)" }}>
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: "var(--accent)", outline: "2px solid var(--ink)" }} />{msgText(m)}
                  </div>)}
            {snap?.running && <div className="typing py-1" style={{ color: "var(--ink)" }}><span /><span /><span /></div>}
            <div ref={end} />
          </div>
          {pending && (pending.type === "question" || pending.gate === "intake") && (
            <div className="pt-3">
              <ApprovalDock key={pending.interrupt_id} item={pending} clientId={cid}
                onDone={() => { reload(); if (pending.gate === "intake") setTimeout(() => router.push(`/run?client=${cid}`), 800); }} />
            </div>
          )}
        </section>
        <div className="flex flex-col gap-4">
          {editing && snap?.values?.brief
            ? <BriefEditor cid={cid} brief={snap.values.brief} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); reload(); }} />
            : <BriefCard b={liveBrief} live onEdit={pending?.gate === "intake" ? () => setEditing(true) : undefined} />}
          {snap?.values?.context_doc && (
            <details className="card">
              <summary className="title cursor-pointer">Product-marketing context (shared with every agent)</summary>
              <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed" style={{ color: "var(--text-2)" }}>{snap.values.context_doc}</pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
