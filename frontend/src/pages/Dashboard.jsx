import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChevronRight, Search, X } from "lucide-react";
import api from "../api";
import KpiCard from "../components/KpiCard";
import StatusBadge from "../components/StatusBadge";
import { formatMoney, Money, MoneyMap } from "../components/Money";

const STATUS_COLORS = {
  OPEN: "#64748B", EVALUATED: "#2563EB", APPROVAL_PENDING: "#EA580C",
  ACTION_SCHEDULED: "#2563EB", ACTION_EXECUTED: "#2563EB", VERIFYING: "#2563EB",
  VERIFIED_RECOVERED: "#16A34A", NATURALLY_RECOVERED: "#059669",
  NOT_RECOVERED: "#DC2626", STOPPED: "#475569", INVALID: "#94A3B8",
};

const EMPTY_FILTERS = { q: "", status: "", outcome: "", policy: "", source: "", stage: "", attributed_action: "", sort: "newest" };

const fmtMap = (m) => {
  const entries = Object.entries(m || {});
  if (!entries.length) return "—";
  return entries.map(([c, v]) => formatMoney(v, c === "UNKNOWN" ? null : c)).join(" · ");
};

const labelize = (s) => (s || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (ch) => ch.toUpperCase());

function Skeleton({ className }) {
  return <div className={`animate-pulse rounded-lg bg-slate-200 ${className}`} />;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [days, setDays] = useState(30);
  const [currency, setCurrency] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [cases, setCases] = useState(null);
  const [ledger, setLedger] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const tableRef = useRef(null);

  const loadSummary = useCallback(() => {
    api.get("/dashboard/summary", { params: { days } }).then((res) => {
      setSummary(res.data);
      setUpdatedAt(new Date());
      const ccys = res.data.charts.currencies;
      if (ccys.length && !ccys.includes(currency)) setCurrency(ccys.includes("INR") ? "INR" : ccys[0]);
    }).catch(() => {});
  }, [days, currency]);

  const loadCases = useCallback(() => {
    const params = {};
    Object.entries(filters).forEach(([k, v]) => { if (v) params[k] = v; });
    api.get("/cases", { params }).then((res) => setCases(res.data.cases)).catch(() => setCases([]));
  }, [filters]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => {
    const t = setTimeout(loadCases, 250);
    return () => clearTimeout(t);
  }, [loadCases]);

  const drillTo = (patch) => {
    setFilters({ ...EMPTY_FILTERS, ...patch });
    setTimeout(() => tableRef.current?.scrollIntoView({ behavior: "smooth" }), 60);
  };

  const openLedger = () => {
    setLedger({ loading: true, entries: [], totals: {} });
    api.get("/dashboard/cost-ledger")
      .then((res) => setLedger({ loading: false, ...res.data }))
      .catch(() => setLedger(null));
  };

  if (!summary) {
    return (
      <div className="space-y-6" data-testid="dashboard-loading">
        <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-6">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { kpis, funnel, charts, policy_activity: policyActivity } = summary;
  const series = (charts.timeseries && currency && charts.timeseries[currency]) || [];
  const hasSeriesData = series.some((p) => p.at_risk > 0 || p.verified_recovered > 0);
  const rbaData = charts.recovery_by_action
    .map((r) => ({ raw: r.action, action: labelize(r.action), amount: r.amounts[currency] }))
    .filter((r) => r.amount !== undefined && r.amount !== null);
  const activeBreakdownText = Object.entries(kpis.active_breakdown || {})
    .map(([s, n]) => `${labelize(s)} ${n}`).join(" · ");

  return (
    <div className="space-y-10" data-testid="dashboard">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-3xl font-medium tracking-tight text-slate-900">Recovery Dashboard</h1>
          <p className="mt-1 text-sm text-slate-500">
            Traceable revenue at risk, verified outcomes, and controlled recovery actions.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2" data-testid="source-summary">
            {charts.sources.map((s) => (
              <span key={s.source} className="flex items-center gap-1.5">
                <StatusBadge value={s.source} />
                <span className="text-xs text-slate-500 tabular-nums">{s.count}</span>
              </span>
            ))}
            {charts.sources.length === 0 && <span className="text-xs text-slate-400">No data sources yet</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="flex justify-end gap-1" data-testid="range-selector">
            {[7, 30, 90].map((d) => (
              <button key={d} onClick={() => setDays(d)} data-testid={`range-${d}d-btn`}
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors duration-200 ${days === d ? "bg-slate-900 text-white" : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                {d}D
              </button>
            ))}
          </div>
          <div className="mt-2 text-xs text-slate-400" data-testid="last-updated">
            Last updated: {updatedAt ? updatedAt.toLocaleTimeString("en-GB") : "—"}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-6">
        <KpiCard label="Revenue Currently at Risk" accent="border-l-amber-500" testId="kpi-revenue-at-risk"
          onClick={() => drillTo({ stage: "at_risk" })}
          tooltip="Sum of amount-at-risk over cases in an active lifecycle state (Open, Evaluated, Approval pending, Action executed, Verifying). Invalid, stopped and closed cases are excluded. Currencies are always reported separately — never converted or blended. Click to see every contributing case."
          sub={<>{kpis.revenue_at_risk_cases} contributing case{kpis.revenue_at_risk_cases === 1 ? "" : "s"} · Projected recoverable (estimate only, never counted): {fmtMap(kpis.expected_recoverable_estimate)} · <span className="font-medium text-slate-700">View cases →</span></>}>
          <MoneyMap amounts={kpis.revenue_at_risk} />
        </KpiCard>

        <KpiCard label="Verified Recovery" accent="border-l-green-500" testId="kpi-verified-gross"
          onClick={() => drillTo({ stage: "recovered" })}
          tooltip="Only cases where a successful settlement was independently observed in source-of-truth payment data AFTER a system action executed. Sent links, reminders and predictions count as zero. Click to see the contributing cases. All-time period."
          sub={<>{kpis.verified_recovered_count} verified recover{kpis.verified_recovered_count === 1 ? "y" : "ies"} · All time · Natural recoveries tracked separately, never counted: {fmtMap(kpis.natural_recovered_not_counted)}</>}>
          <MoneyMap amounts={kpis.verified_gross_recovery} />
        </KpiCard>

        <KpiCard label="Verified Net Recovery" accent="border-l-green-600" testId="kpi-verified-net"
          onClick={openLedger}
          tooltip="Verified Recovery minus all executed action costs. Every cost is a flat catalog cost recorded on an executed action record — click to open the full cost/recovery ledger and inspect each line. A negative currency line means action costs exceeded verified recovery in that currency."
          sub={<>Gross {fmtMap(kpis.verified_gross_recovery)} − costs {fmtMap(kpis.action_costs)} ({kpis.executed_action_count} executed actions) · <span className="font-medium text-slate-700">Cost ledger →</span></>}>
          <MoneyMap amounts={kpis.verified_net_recovery} />
        </KpiCard>

        <KpiCard label="Active Cases" accent="border-l-blue-500" testId="kpi-active-cases"
          onClick={() => drillTo({ stage: "at_risk" })}
          tooltip="Cases in an active lifecycle state right now (Open, Evaluated, Approval pending, Action executed, Verifying). Click to inspect them."
          sub={activeBreakdownText || "No active cases"}>
          {kpis.active_cases}
        </KpiCard>

        <KpiCard label="Verified Recovery Rate" accent="border-l-blue-600" testId="kpi-recovery-rate"
          onClick={kpis.recovery_rate_pct !== null ? () => drillTo({ stage: "recovered" }) : undefined}
          tooltip="Formula: verified recovered cases ÷ cases with a known final outcome (verified recovered + naturally recovered + not recovered). Stopped and invalid cases are excluded from the denominator — they are not resolution outcomes."
          sub={kpis.recovery_rate_pct !== null
            ? <>{kpis.recovery_rate_numerator} of {kpis.recovery_rate_denominator} cases with a known final outcome · {kpis.active_cases} still unresolved</>
            : "No cases with a known final outcome yet"}>
          {kpis.recovery_rate_pct === null ? (
            <span className="text-base text-slate-400">Insufficient data</span>
          ) : `${kpis.recovery_rate_pct}%`}
        </KpiCard>

        <KpiCard label="Exceptions" accent="border-l-red-500" testId="kpi-exceptions"
          onClick={() => navigate("/review")}
          tooltip="Records that failed validation (bad amounts, unsupported statuses, missing linkage, duplicates). They are excluded from every financial total until resolved. Click to inspect each one in the review queue."
          sub="Excluded from all financial totals · Inspect →">
          {kpis.exceptions_open}
        </KpiCard>
      </div>

      <section data-testid="recovery-funnel" className="rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-heading text-lg font-medium text-slate-900">Recovery Funnel</h2>
          <span className="text-xs text-slate-400">Cumulative “reached this stage” counts — every case in a later stage genuinely passed the earlier ones.</span>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-y-3">
          {funnel.order.map((stage, i) => (
            <div key={stage} className="flex items-center">
              <button
                data-testid={`funnel-stage-${stage}`}
                onClick={() => drillTo({ stage })}
                title={funnel.meta[stage]?.description}
                className={`rounded-lg border px-4 py-2 text-center transition-colors duration-200 hover:border-slate-400 ${stage === "recovered" ? "border-green-200 bg-green-50" : "border-slate-200 bg-slate-50"}`}
              >
                <div className={`text-xl font-semibold tabular-nums ${stage === "recovered" ? "text-green-700" : "text-slate-900"}`}>
                  {funnel.stages[stage] ?? 0}
                </div>
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">{funnel.meta[stage]?.label || stage}</div>
              </button>
              {i < funnel.order.length - 1 && <ChevronRight className="mx-1 h-4 w-4 text-slate-300" />}
            </div>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4" data-testid="funnel-side-stats">
          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Outside the funnel:</span>
          <button data-testid="side-stopped" onClick={() => drillTo({ stage: "stopped" })} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">Stopped {funnel.side.stopped}</button>
          <button data-testid="side-invalid" onClick={() => drillTo({ stage: "invalid" })} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50">Invalid {funnel.side.invalid}</button>
          <button data-testid="side-blocked" onClick={() => drillTo({ stage: "blocked" })} className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-xs text-red-700 hover:bg-red-100">Blocked by policy {funnel.side.blocked}</button>
          <button data-testid="side-exceptions" onClick={() => navigate("/review")} className="rounded-md border border-red-200 bg-red-50 px-2.5 py-1 text-xs text-red-700 hover:bg-red-100">Exceptions {kpis.exceptions_open}</button>
        </div>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-xl border border-slate-200 bg-white p-6 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-heading text-lg font-medium text-slate-900">At Risk Detected vs Verified Recovery</h2>
              <p className="mt-0.5 text-xs text-slate-400">Cases created per day (amber) vs independently verified recovery per day (green) · last {days} days · per currency, never converted</p>
            </div>
            {charts.currencies.length > 0 && (
              <div className="flex gap-1" data-testid="currency-toggle">
                {charts.currencies.map((c) => (
                  <button key={c} onClick={() => setCurrency(c)} data-testid={`currency-tab-${c}`}
                    className={`rounded-md px-3 py-1 text-xs font-medium transition-colors duration-200 ${currency === c ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                    {c}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="mt-4 h-64">
            {hasSeriesData ? (
              <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                <AreaChart data={series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#94A3B8" }} tickFormatter={(d) => d.slice(5)} />
                  <YAxis tick={{ fontSize: 11, fill: "#94A3B8" }} />
                  <Tooltip formatter={(value, name) => [formatMoney(value, currency), name === "at_risk" ? "Revenue at Risk (detected)" : "Verified Recovered"]} labelFormatter={(d) => `Date: ${d}`} />
                  <Area type="monotone" dataKey="at_risk" stroke="#D97706" fill="#FEF3C7" strokeWidth={2} name="at_risk" />
                  <Area type="monotone" dataKey="verified_recovered" stroke="#16A34A" fill="#DCFCE7" strokeWidth={2} name="verified_recovered" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart text={currency ? `No at-risk or verified recovery activity in ${currency} in the last ${days} days.` : "No data yet. Ingest a file or send a simulated event."} />
            )}
          </div>
        </div>

        <ChartCard title="Verified Recovery by Action" testId="chart-recovery-by-action"
          subtitle="Only independently verified recoveries, attributed to the action that executed before settlement. Click a bar to see its cases.">
          {rbaData.length ? (
            <BarChart data={rbaData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis dataKey="action" tick={{ fontSize: 10, fill: "#94A3B8" }} />
              <YAxis tick={{ fontSize: 11, fill: "#94A3B8" }} />
              <Tooltip formatter={(v) => [formatMoney(v, currency), `Verified recovered (${currency})`]} />
              <Bar dataKey="amount" fill="#16A34A" radius={[4, 4, 0, 0]} cursor="pointer"
                onClick={(data) => data?.raw && drillTo({ attributed_action: data.raw })} />
            </BarChart>
          ) : <EmptyChart text={`No verified recoveries attributed to actions in ${currency || "this currency"} yet. Executed actions count as zero until independently verified.`} />}
        </ChartCard>

        <ChartCard title="Cases by Status" testId="chart-cases-by-status" raw
          subtitle="Every case exactly once. Click a segment or legend entry to filter the case table.">
          {charts.cases_by_status.length ? (
            <>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%" minWidth={0}>
                  <PieChart>
                    <Pie data={charts.cases_by_status} dataKey="count" nameKey="status" innerRadius={45} outerRadius={75} paddingAngle={2}
                      onClick={(data) => data?.status && drillTo({ status: data.status })} cursor="pointer">
                      {charts.cases_by_status.map((entry) => (
                        <Cell key={entry.status} fill={STATUS_COLORS[entry.status] || "#94A3B8"} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, name) => [v, labelize(name)]} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-3 flex flex-wrap items-start gap-2 self-start" data-testid="status-legend">
                {charts.cases_by_status.map((entry) => (
                  <button key={entry.status} onClick={() => drillTo({ status: entry.status })}
                    className="flex items-center gap-1.5 self-start rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50">
                    <span className="h-2 w-2 rounded-full" style={{ background: STATUS_COLORS[entry.status] || "#94A3B8" }} />
                    {labelize(entry.status)} <span className="font-semibold tabular-nums">{entry.count}</span>
                  </button>
                ))}
              </div>
            </>
          ) : <EmptyChart text="No cases yet." />}
        </ChartCard>

        <ChartCard title="Failure Reasons" testId="chart-failure-reasons"
          subtitle="Human-readable labels from actual failed payment records. Raw provider codes shown in tooltips.">
          {charts.failure_reasons.length ? (
            <BarChart data={charts.failure_reasons} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis type="number" tick={{ fontSize: 11, fill: "#94A3B8" }} allowDecimals={false} />
              <YAxis type="category" dataKey="label" width={150} tick={{ fontSize: 10, fill: "#64748B" }} />
              <Tooltip formatter={(v, name, item) => [`${v} attempts`, `raw code: ${item.payload.code}`]} labelFormatter={() => ""} />
              <Bar dataKey="count" fill="#2563EB" radius={[0, 4, 4, 0]} />
            </BarChart>
          ) : <EmptyChart text="No failed payments recorded yet." />}
        </ChartCard>

        <ChartCard title="Policy Control Activity" testId="chart-policy-blocks" raw
          subtitle="Every decision the deterministic policy engine has made, from the immutable audit trail.">
          {policyActivity.total_decisions > 0 ? (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap gap-3" data-testid="policy-decision-counts">
                {policyActivity.decisions.map((d) => (
                  <div key={d.decision} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                    <StatusBadge value={d.decision} />
                    <span className="text-lg font-semibold tabular-nums text-slate-900">{d.count}</span>
                  </div>
                ))}
              </div>
              <div className="text-xs text-slate-500">
                {policyActivity.human_approvals} human approvals · {policyActivity.human_rejections} human rejections · {policyActivity.approvals_required} approval requests raised
              </div>
              {policyActivity.block_rules.length > 0 && (
                <div>
                  <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500 mb-2">Active block / stop rules on open cases</div>
                  <ul className="space-y-1">
                    {policyActivity.block_rules.map((r) => (
                      <li key={r.rule} className="flex justify-between text-sm text-slate-600">
                        <span className="font-mono text-xs">{r.rule}</span>
                        <span className="tabular-nums font-medium">{r.count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <EmptyChart text="No policy decisions recorded yet. Every ALLOW / BLOCK / APPROVAL / STOP decision the engine makes will appear here." />
          )}
        </ChartCard>
      </section>

      <section ref={tableRef} className="rounded-xl border border-slate-200 bg-white scroll-mt-6" data-testid="cases-section">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-6">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="font-heading text-lg font-medium text-slate-900">Recovery Cases</h2>
            {filters.stage && (
              <span className="flex items-center gap-1.5 rounded-md bg-blue-50 border border-blue-200 px-2 py-1 text-xs text-blue-700" data-testid="stage-filter-chip">
                Stage: {funnel.meta[filters.stage]?.label || labelize(filters.stage)}
                <button onClick={() => setFilters((f) => ({ ...f, stage: "" }))} data-testid="clear-stage-filter" className="text-blue-400 hover:text-blue-700"><X className="h-3 w-3" /></button>
              </span>
            )}
            {filters.attributed_action && (
              <span className="flex items-center gap-1.5 rounded-md bg-green-50 border border-green-200 px-2 py-1 text-xs text-green-700" data-testid="action-filter-chip">
                Recovered via {labelize(filters.attributed_action)}
                <button onClick={() => setFilters((f) => ({ ...f, attributed_action: "" }))} className="text-green-400 hover:text-green-700"><X className="h-3 w-3" /></button>
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
              <input data-testid="case-search-input" value={filters.q}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                placeholder="Search title, order, payment…"
                className="w-full sm:w-56 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 outline-none transition-colors duration-200 focus:border-slate-400" />
            </div>
            <select data-testid="case-status-filter" value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
              <option value="">All states</option>
              {["OPEN", "EVALUATED", "APPROVAL_PENDING", "ACTION_EXECUTED", "VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "STOPPED", "INVALID"].map((s) => (
                <option key={s} value={s}>{labelize(s)}</option>
              ))}
            </select>
            <select data-testid="case-source-filter" value={filters.source} onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))}
              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
              <option value="">All sources</option>
              {["LIVE", "TEST_MODE", "IMPORTED", "SIMULATED"].map((s) => <option key={s} value={s}>{labelize(s)}</option>)}
            </select>
            <select data-testid="case-outcome-filter" value={filters.outcome} onChange={(e) => setFilters((f) => ({ ...f, outcome: e.target.value }))}
              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
              <option value="">All outcomes</option>
              {["VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "PENDING", "STOPPED", "INVALID_CASE"].map((s) => (
                <option key={s} value={s}>{labelize(s)}</option>
              ))}
            </select>
            <select data-testid="case-policy-filter" value={filters.policy} onChange={(e) => setFilters((f) => ({ ...f, policy: e.target.value }))}
              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
              <option value="">All policy</option>
              {["ALLOW", "APPROVAL", "BLOCK", "STOP"].map((s) => <option key={s} value={s}>{labelize(s)}</option>)}
            </select>
            <select data-testid="case-sort-select" value={filters.sort} onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value }))}
              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="amount_desc">Amount ↓ (grouped by currency)</option>
              <option value="amount_asc">Amount ↑ (grouped by currency)</option>
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="cases-table">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                <th className="px-6 py-3">Transaction</th>
                <th className="px-6 py-3 text-right">Amount at Risk</th>
                <th className="px-6 py-3">Why at Risk</th>
                <th className="px-6 py-3">Recommended Action</th>
                <th className="px-6 py-3">Policy</th>
                <th className="px-6 py-3">Current State</th>
                <th className="px-6 py-3">Outcome</th>
                <th className="px-6 py-3">Source</th>
              </tr>
            </thead>
            <tbody>
              {(cases || []).map((c) => (
                <tr key={c.case_id} data-testid={`case-row-${c.case_id}`}
                  onClick={() => navigate(`/cases/${c.case_id}`)}
                  className="cursor-pointer border-b border-slate-100 transition-colors duration-200 hover:bg-slate-50">
                  <td className="px-6 py-3">
                    <div className="font-medium text-slate-900">{c.title}</div>
                    <div className="font-mono text-[11px] text-slate-400">{c.order_key}{c.customer_reference ? ` · ${c.customer_reference}` : ""}</div>
                  </td>
                  <td className="px-6 py-3 text-right"><Money amount={c.amount_at_risk} currency={c.currency} className="text-slate-900" /></td>
                  <td className="px-6 py-3 max-w-[220px]"><span className="block truncate text-xs text-slate-600" title={c.why_at_risk}>{c.why_at_risk}</span></td>
                  <td className="px-6 py-3 text-slate-600 text-xs">{c.recommended_action ? labelize(c.recommended_action) : "—"}</td>
                  <td className="px-6 py-3"><StatusBadge value={c.policy_result?.decision} /></td>
                  <td className="px-6 py-3"><StatusBadge value={c.status} /></td>
                  <td className="px-6 py-3"><StatusBadge value={c.outcome} /></td>
                  <td className="px-6 py-3"><StatusBadge value={c.source_category} /></td>
                </tr>
              ))}
              {cases && cases.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-6 py-12 text-center">
                    <p className="text-sm text-slate-400">
                      {Object.values(filters).some((v) => v && v !== "newest")
                        ? "No cases match these filters."
                        : "No recovery cases yet. Ingest merchant data or send a simulated payment event to get started."}
                    </p>
                    {Object.values(filters).some((v) => v && v !== "newest") && (
                      <button onClick={() => setFilters(EMPTY_FILTERS)} data-testid="clear-all-filters"
                        className="mt-3 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600 hover:bg-slate-50">
                        Clear all filters
                      </button>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {ledger && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={() => setLedger(null)} data-testid="cost-ledger-modal">
          <div className="max-h-[85vh] w-full max-w-3xl overflow-auto rounded-xl border border-slate-200 bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-100 p-6">
              <div>
                <h3 className="font-heading text-lg font-medium text-slate-900">Cost & Recovery Ledger</h3>
                <p className="mt-1 text-xs text-slate-500">
                  Every executed action with its recorded catalog cost. Verified Net Recovery = Verified Recovery − these costs, per currency.
                  SIMULATED executions are planning estimates, not real charges.
                </p>
              </div>
              <button onClick={() => setLedger(null)} data-testid="close-ledger-btn" className="rounded-md p-2 text-slate-400 hover:bg-slate-100"><X className="h-4 w-4" /></button>
            </div>
            <div className="p-6">
              <div className="flex flex-wrap gap-3 text-sm">
                <span className="font-medium text-slate-700">Total action costs:</span>
                <MoneyMap amounts={ledger.totals} lineClass="text-sm" />
              </div>
              {ledger.loading ? (
                <div className="mt-6 h-32 animate-pulse rounded-lg bg-slate-100" />
              ) : ledger.entries.length === 0 ? (
                <p className="mt-6 rounded-lg border border-dashed border-slate-200 bg-slate-50 p-6 text-center text-sm text-slate-400">
                  No executed actions yet — so no action costs exist. Net recovery equals verified recovery.
                </p>
              ) : (
                <table className="mt-4 w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                      <th className="py-2 pr-4">Case</th>
                      <th className="py-2 pr-4">Action</th>
                      <th className="py-2 pr-4">Mode</th>
                      <th className="py-2 pr-4">Case Outcome</th>
                      <th className="py-2 text-right">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ledger.entries.map((e) => (
                      <tr key={e.action_id} className="border-b border-slate-100">
                        <td className="py-2.5 pr-4">
                          <button onClick={() => { setLedger(null); navigate(`/cases/${e.case_id}`); }} className="text-left text-xs font-medium text-slate-800 hover:underline">
                            {e.case_title}
                          </button>
                          <div className="font-mono text-[10px] text-slate-400">{e.provider_reference}</div>
                        </td>
                        <td className="py-2.5 pr-4 text-xs text-slate-600">{e.label}</td>
                        <td className="py-2.5 pr-4"><StatusBadge value={e.execution_mode} /></td>
                        <td className="py-2.5 pr-4"><StatusBadge value={e.case_status} /></td>
                        <td className="py-2.5 text-right"><Money amount={e.estimated_cost} currency={e.currency} className="text-slate-900" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ChartCard({ title, subtitle, testId, raw = false, children }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6" data-testid={testId}>
      <h3 className="font-heading text-base font-medium text-slate-900">{title}</h3>
      {subtitle && <p className="mt-1 text-xs text-slate-400 leading-relaxed">{subtitle}</p>}
      {raw ? (
        <div className="mt-4">{children}</div>
      ) : (
        <div className="mt-4 h-56">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>{children}</ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function EmptyChart({ text }) {
  return (
    <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50">
      <p className="max-w-xs text-center text-sm text-slate-400">{text}</p>
    </div>
  );
}
