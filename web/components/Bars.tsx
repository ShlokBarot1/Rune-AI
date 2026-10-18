"use client";

/** Rounded bar chart: ink bars, the latest one in lime (the live accent). */
export default function Bars({
  data,
  height = 72,
  format = (v: number) => String(Math.round(v)),
}: {
  data: { label: string; value: number }[];
  height?: number;
  format?: (v: number) => string;
}) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div className="flex items-end gap-2" style={{ height: height + 18 }} role="img" aria-label="bar chart">
      {data.map((d, i) => {
        const latest = i === data.length - 1;
        return (
          <div key={d.label + i} className="flex flex-1 flex-col items-center gap-1.5" title={`${d.label}: ${format(d.value)}`}>
            <div className="relative w-full max-w-[18px] rounded-full" style={{ height, background: "var(--bar)" }}>
              <div className="absolute bottom-0 w-full rounded-full transition-all duration-700"
                style={{ height: `${Math.max(8, (d.value / max) * 100)}%`, background: latest ? "var(--accent)" : "var(--ink)" }} />
            </div>
            <span className="meta" style={{ fontSize: 10 }}>{d.label}</span>
          </div>
        );
      })}
    </div>
  );
}
