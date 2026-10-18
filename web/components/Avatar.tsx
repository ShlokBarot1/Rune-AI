/** Agent avatar: colour by team (malav / shlok / auditor / human), initials from the agent name. */
export default function Avatar({ name }: { name: string }) {
  const team = name.split(".")[0];
  const bg = team === "malav" ? "#e8d9c4" : team === "shlok" ? "#cfdcef" : team === "auditor" ? "#dcefe1" : team === "human" ? "#f3dde0" : "#e3e8ef";
  const initials = name.split(".").pop()!.slice(0, 2).toUpperCase();
  return (
    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-[10.5px] font-semibold" style={{ background: bg, color: "#1f2d4d" }}>
      {initials}
    </span>
  );
}
