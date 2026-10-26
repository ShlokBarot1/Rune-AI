"use client";
import { useEffect, useState } from "react";
import { Save, Trash2, UserPlus } from "lucide-react";
import { fileUrl, get, post, usePoll, type Json } from "@/lib/api";
import { uploadFile } from "@/lib/run";

const ROLES = [["strategist", "Strategist · runs campaigns"], ["client", "Client · sees and approves their own"], ["owner", "Owner · everything"]];

/** The workspace owner: white-label branding, and who can see which campaigns. */
export default function Settings() {
  const [brand, setBrand] = useState<Json>({ agency_name: "", color: "#D4F34A", logo: null });
  const [users, , reload] = usePoll<Json[]>("/auth/users", 20000);
  const [runs, , reloadRuns] = usePoll<Json[]>("/runs", 20000);
  const [msg, setMsg] = useState<string | null>(null);
  const label = (r: Json) => (runs ?? []).filter((x) => x.name === r.name).length > 1 ? `${r.name} · ${r.client_id.slice(-4)}` : r.name;
  const [nu, setNu] = useState<Json>({ email: "", name: "", role: "client", password: "", clients: [] });
  useEffect(() => { get("/settings/public").then(setBrand).catch(() => {}); }, []);

  const saveBrand = async () => {
    try { setBrand(await post("/settings", brand)); setMsg("Branding saved: reports, the portal and the sidebar use it."); setTimeout(() => window.location.reload(), 700); }
    catch (e) { setMsg(String((e as Error).message)); }
  };
  const add = async () => {
    try { await post("/auth/users", nu); setNu({ email: "", name: "", role: "client", password: "", clients: [] }); setMsg("Account created. Share the email and password with them."); reload(); }
    catch (e) { setMsg(String((e as Error).message)); }
  };
  const access = async (u: Json, cid: string) => {
    const clients = u.clients.includes(cid) ? u.clients.filter((c: string) => c !== cid) : [...u.clients, cid];
    await post(`/auth/users/${u.id}`, { clients }); reload();
  };
  const removeRun = async (r: Json) => {
    if (!confirm(`Remove “${label(r)}” for good? Its Meta campaign, creatives, logs and results are deleted.`)) return;
    try {
      const out = await post(`/runs/${r.client_id}/delete`, {});
      setMsg(`Removed ${label(r)}${out.meta_campaigns ? ` and its ${out.meta_campaigns} Meta campaign(s)` : ""}.`); reloadRuns();
    } catch (e) { setMsg(String((e as Error).message)); }
  };

  return (
    <div className="flex flex-col gap-5">
      <div><div className="label">Workspace</div><h1 className="page-title mt-1">Branding &amp; people</h1></div>
      {msg && <div className="pill blue !whitespace-normal">{msg}</div>}

      <section className="card flex flex-col gap-3">
        <div className="title">White-label</div>
        <p className="meta">Your agency's name, logo and colour on the dashboard, the client portal and every report.</p>
        <div className="flex flex-wrap items-center gap-3">
          {brand.logo && <img src={brand.logo.startsWith("http") ? brand.logo : fileUrl(brand.logo)} alt="" className="h-10 object-contain" />}
          <label className="btn btn-ghost !h-9 cursor-pointer">Upload logo
            <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
              onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) setBrand({ ...brand, logo: await uploadFile("agency", f) }); }} />
          </label>
          <input className="input !w-64" placeholder="Agency name" value={brand.agency_name ?? ""} onChange={(e) => setBrand({ ...brand, agency_name: e.target.value })} />
          <input type="color" className="h-9 w-12 cursor-pointer rounded-lg border-0 bg-transparent" value={brand.color ?? "#D4F34A"} onChange={(e) => setBrand({ ...brand, color: e.target.value })} title="Brand colour" />
          <button className="btn btn-primary !h-9" onClick={saveBrand}><Save size={14} /> Save</button>
        </div>
      </section>

      <section className="card flex flex-col gap-3">
        <div className="title">People</div>
        {(users ?? []).map((u) => (
          <div key={u.id} className="tile flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{u.name}</span><span className="meta">{u.email}</span><span className="pill">{u.role}</span>
              <button className="circle sm ml-auto" title="Remove" onClick={async () => { await post(`/auth/users/${u.id}/delete`, {}); reload(); }}><Trash2 size={13} /></button>
            </div>
            {u.role !== "owner" && (
              <div className="flex flex-wrap gap-1.5">
                <span className="meta mr-1">{u.role === "strategist" && !u.clients.length ? "All campaigns · or limit to:" : "Campaigns:"}</span>
                {(runs ?? []).map((r) => (
                  <button key={r.client_id} className={`pill !py-1 ${u.clients.includes(r.client_id) ? "dark" : ""}`} onClick={() => access(u, r.client_id)}>{label(r)}</button>
                ))}
              </div>
            )}
          </div>
        ))}
        <div className="grid gap-2 sm:grid-cols-5">
          <input className="input" placeholder="Name" value={nu.name} onChange={(e) => setNu({ ...nu, name: e.target.value })} />
          <input className="input" type="email" placeholder="Email" value={nu.email} onChange={(e) => setNu({ ...nu, email: e.target.value })} />
          <input className="input" type="password" placeholder="Password (8+)" value={nu.password} onChange={(e) => setNu({ ...nu, password: e.target.value })} />
          <select className="input" value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <button className="btn btn-lime" disabled={!nu.email || nu.password.length < 8} onClick={add}><UserPlus size={14} /> Add</button>
        </div>
        {nu.role === "client" && (
          <div className="flex flex-wrap gap-1.5"><span className="meta mr-1">Their campaigns:</span>
            {(runs ?? []).map((r) => (
              <button key={r.client_id} className={`pill !py-1 ${nu.clients.includes(r.client_id) ? "dark" : ""}`}
                onClick={() => setNu({ ...nu, clients: nu.clients.includes(r.client_id) ? nu.clients.filter((c: string) => c !== r.client_id) : [...nu.clients, r.client_id] })}>{label(r)}</button>
            ))}
          </div>
        )}
      </section>

      <section className="card flex flex-col gap-3">
        <div className="title">Campaigns</div>
        <p className="meta">Removing one deletes it in Meta too (when it was really created there), plus its creatives, logs and results. It can't be undone.</p>
        {(runs ?? []).map((r) => (
          <div key={r.client_id} className="tile flex flex-wrap items-center gap-2">
            <span className="font-semibold">{label(r)}</span><span className="meta">{r.client_id}</span>
            {r.live && <span className="pill">live</span>}{r.running && <span className="pill">running</span>}
            <button className="circle sm ml-auto" title={r.running ? "Stop the run first" : "Remove campaign"} disabled={r.running}
              onClick={() => removeRun(r)}><Trash2 size={13} /></button>
          </div>
        ))}
      </section>
    </div>
  );
}
