"use client";

type Props = {
  name: string;
  status: "running" | "done";
  output?: string;
};

export default function ToolCallBadge({ name, status, output }: Props) {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-mono border border-neutral-700 bg-neutral-900 text-neutral-400 my-1">
      {status === "running" ? (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
      ) : (
        <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
      )}
      <span>{name}</span>
      {output && status === "done" && (
        <span className="text-neutral-600 max-w-[200px] truncate">→ {output}</span>
      )}
    </div>
  );
}
