"use client";
import { useEffect, useState } from "react";
import { LogIn, Sparkles } from "lucide-react";
import { API, type Json } from "@/lib/api";

/** Sign in (agency mode). The first visit to a new workspace creates its owner account. */
export default function Login() {
  const [status, setStatus] = useState<Json | null>(null);
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { fetch(`${API}/auth/status`).then((r) => r.json()).then(setStatus).catch(() => setStatus({ enabled: false })); }, []);

  const go = async () => {
    setBusy(true); setErr(null);
    try {
      const setup = status?.setup_needed;
      const r = await fetch(`${API}/auth/${setup ? "setup" : "login"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(setup ? { ...form, role: "owner" } : { email: form.email, password: form.password }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? "Sign-in failed");
      localStorage.setItem("rune_token", body.token);
      const me = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${body.token}` } }).then((x) => x.json());
      window.location.href = me.user?.role === "client" ? "/portal" : "/run";
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  };

  const brand = status?.branding ?? {};
  if (status && !status.enabled) return (
    <div className="mx-auto max-w-md pt-20 text-center">
      <h1 className="page-title">Sign-in is off</h1>
      <p className="meta mt-2">This workspace runs open (local use). Set RUNE_AUTH=1 on the API to require accounts.</p>
      <a className="btn btn-primary mt-4" href="/run">Go to Mission Control</a>
    </div>
  );
  return (
    <div className="mx-auto flex max-w-sm flex-col gap-4 pt-16">
      <div className="flex flex-col items-center gap-2 text-center">
        {brand.logo
          ? <img src={brand.logo.startsWith("http") ? brand.logo : `${API}/files/${brand.logo}`} alt="" className="h-12 object-contain" />
          : <span className="grid h-12 w-12 place-items-center rounded-full" style={{ background: "var(--ink)", color: brand.color ?? "var(--accent)" }}><Sparkles size={20} /></span>}
        <h1 className="page-title !text-[28px]">{status?.setup_needed ? "Create the workspace owner" : `Sign in to ${brand.agency_name ?? "Rune"}`}</h1>
      </div>
      <section className="card flex flex-col gap-3">
        {status?.setup_needed && <input className="input" placeholder="Your name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}
        <input className="input" type="email" autoComplete="email" placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input className="input" type="password" autoComplete={status?.setup_needed ? "new-password" : "current-password"} placeholder="Password (8+ characters)"
          value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} onKeyDown={(e) => e.key === "Enter" && go()} />
        <button className="btn btn-primary" disabled={busy || !form.email || form.password.length < 8} onClick={go}>
          <LogIn size={15} /> {busy ? "…" : status?.setup_needed ? "Create & sign in" : "Sign in"}
        </button>
        {err && <div className="pill bad !whitespace-normal">{err}</div>}
      </section>
    </div>
  );
}
