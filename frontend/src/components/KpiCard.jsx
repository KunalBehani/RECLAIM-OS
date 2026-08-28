import { Info } from "lucide-react";

export default function KpiCard({ label, tooltip, onClick, accent = "border-l-slate-300", testId, children, sub }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      data-testid={testId}
      onClick={onClick}
      className={`relative w-full rounded-xl border border-slate-200 border-l-4 ${accent} bg-white p-6 text-left transition-transform duration-300 ease-out hover:-translate-y-1 hover:shadow-md hover:border-slate-300 ${onClick ? "cursor-pointer" : ""}`}
    >
      <div className="flex items-center gap-1.5">
        <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">{label}</div>
        {tooltip && (
          <span className="group relative inline-flex" onClick={(e) => e.stopPropagation()}>
            <Info className="h-3.5 w-3.5 text-slate-300 transition-colors duration-200 hover:text-slate-500" />
            <span className="pointer-events-none absolute left-0 top-5 z-30 hidden w-72 rounded-lg border border-slate-200 bg-white p-3 text-xs font-normal normal-case tracking-normal leading-relaxed text-slate-600 shadow-lg group-hover:block">
              {tooltip}
            </span>
          </span>
        )}
      </div>
      <div className="mt-3 text-2xl font-medium tracking-tight text-slate-900 tabular-nums">
        {children}
      </div>
      {sub && <div className="mt-2 text-xs text-slate-500 leading-relaxed">{sub}</div>}
    </Tag>
  );
}
