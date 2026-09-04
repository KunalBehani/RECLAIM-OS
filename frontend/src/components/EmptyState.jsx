export default function EmptyState({ title, hint, testId, children }) {
  return (
    <div data-testid={testId} className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <div className="font-heading text-base font-medium text-slate-700">{title}</div>
      {hint && <p className="mx-auto mt-1.5 max-w-md text-sm leading-relaxed text-slate-400">{hint}</p>}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
