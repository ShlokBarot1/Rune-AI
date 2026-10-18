import { agentColor, agentShort } from "@/lib/run";

/** Agent avatar in the shared agent colours (malav lime, shlok blue, auditor amber, human white). */
export default function Avatar({ name }: { name: string }) {
  const [bg, fg] = agentColor(name);
  return <span className="agent-dot !h-8 !w-8" style={{ background: bg, color: fg, border: bg === "#ffffff" ? "1px solid var(--line)" : undefined }}>{agentShort(name)}</span>;
}
