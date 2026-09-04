export default function PageHeader({ eyebrow, title, subtitle, actions, testId, children }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4" data-testid={testId}>
      <div className="min-w-0 animate-enter">
        {eyebrow && (
          <div className="text-[11px] font-bold uppercase tracking-[0.22em] text-slate-400">{eyebrow}</div>
        )}
        <h1 className="mt-1.5 font-heading text-3xl font-medium tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-slate-500">{subtitle}</p>}
        {children}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  );
}
