"use client";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Globe, Sparkles, Wand2 } from "lucide-react";
import { msgText, post, usePoll, useEvents, type Json } from "@/lib/api";
import { BriefCard } from "@/components/rune/Views";
import ApprovalDock from "@/components/rune/ApprovalDock";

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

  const start = async () => {
    setBusy(true);
    try {
      const r = await post("/runs", { intake: { website: url.trim(), notes } });
      setCid(r.client_id);
      window.history.replaceState(null, "", `/onboarding?client=${r.client_id}`);
    } finally { setBusy(false); }
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
              onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && url && start()} />
          </div>
          <button className="btn btn-primary !h-14 !px-7 !text-[14px]" disabled={!url || busy} onClick={start}>
            <Wand2 size={16} /> Start agents
          </button>
        </div>
        <textarea className="input mt-2 !rounded-[20px] min-h-16" placeholder="Optional: anything we should know — budget, dates, goal, markets…" value={notes} onChange={(e) => setNotes(e.target.value)} />
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
          <div className="-mr-2 flex flex-1 flex-col gap-2 overflow-y-auto pr-2">
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
          <BriefCard b={liveBrief} live />
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
