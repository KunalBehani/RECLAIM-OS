export default function KpiCard({ label, accent = "border-l-slate-300", testId, children, sub }) {
  return (
    <div
      data-testid={testId}
      className={`rounded-xl border border-slate-200 border-l-4 ${accent} bg-white p-6 transition-transform duration-300 ease-out hover:-translate-y-1 hover:shadow-md hover:border-slate-300`}
    >
      <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">{label}</div>
      <div className="mt-3 text-2xl sm:text-3xl font-medium tracking-tight text-slate-900 tabular-nums">
        {children}
      </div>
      {sub && <div className="mt-2 text-xs text-slate-500 leading-relaxed">{sub}</div>}
    </div>
  );
}
