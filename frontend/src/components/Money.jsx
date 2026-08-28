export function formatMoney(amount, currency) {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return "—";
  const ccy = currency && currency !== "UNKNOWN" ? currency : null;
  const locale = ccy === "INR" ? "en-IN" : "en-US";
  try {
    if (!ccy) {
      return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(amount);
    }
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: ccy,
      maximumFractionDigits: Number(amount) % 1 === 0 ? 0 : 2,
    }).format(amount);
  } catch {
    return `${ccy || ""} ${amount}`;
  }
}

export function Money({ amount, currency, className = "" }) {
  return (
    <span className={`tabular-nums font-mono ${className}`}>{formatMoney(amount, currency)}</span>
  );
}

export function MoneyMap({ amounts, className = "", lineClass = "" }) {
  const entries = Object.entries(amounts || {});
  if (entries.length === 0) {
    return <span className={`tabular-nums font-mono text-slate-400 ${className}`}>—</span>;
  }
  return (
    <div className={className}>
      {entries.map(([ccy, value]) => (
        <div key={ccy} className={`tabular-nums font-mono ${lineClass}`}>
          {formatMoney(value, ccy === "UNKNOWN" ? null : ccy)}
          {ccy === "UNKNOWN" && <span className="ml-1 text-xs text-slate-400">(no currency)</span>}
        </div>
      ))}
    </div>
  );
}
