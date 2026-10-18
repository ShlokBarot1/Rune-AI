"use client";

/** Thin rounded bars like the reference "Earning Activity" chart; the last bar is highlighted. */
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
        const active = i === data.length - 1;
        return (
          <div key={d.label + i} className="flex flex-1 flex-col items-center gap-1.5" title={`${d.label}: ${format(d.value)}`}>
            <div className="relative w-full max-w-[14px] rounded-full" style={{ height, background: "var(--bar)" }}>
              <div
                className="absolute bottom-0 w-full rounded-full transition-all duration-500"
                style={{ height: `${Math.max(8, (d.value / max) * 100)}%`, background: active ? "var(--primary)" : "color-mix(in srgb, var(--primary) 22%, var(--bar))" }}
              >
                {active && <span className="absolute left-1/2 top-1 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-white" />}
              </div>
            </div>
            <span className="meta" style={{ fontSize: 10 }}>{d.label}</span>
          </div>
        );
      })}
    </div>
  );
}
