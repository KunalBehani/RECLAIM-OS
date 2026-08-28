import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChevronRight, Search } from "lucide-react";
import api from "../api";
import KpiCard from "../components/KpiCard";
import StatusBadge from "../components/StatusBadge";
import { formatMoney, Money, MoneyMap } from "../components/Money";

const STATUS_COLORS = {
  OPEN: "#64748B", EVALUATED: "#2563EB", APPROVAL_PENDING: "#EA580C",
  ACTION_EXECUTED: "#2563EB", VERIFYING: "#2563EB", VERIFIED_RECOVERED: "#16A34A",
  NATURALLY_RECOVERED: "#059669", NOT_RECOVERED: "#DC2626", STOPPED: "#475569", INVALID: "#94A3B8",
};

const FUNNEL_STAGES = ["detected", "eligible", "evaluated", "approved", "executed", "verifying", "recovered"];

function Skeleton({ className }) {
  return <div className={`animate-pulse rounded-lg bg-slate-200 ${className}`} />;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [cases, setCases] = useState(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [currency, setCurrency] = useState(null);

  const loadSummary = useCallback(() => {
    api.get("/dashboard/summary").then((res) => {
      setSummary(res.data);
      const ccys = res.data.charts.currencies;
      if (ccys.length && !currency) setCurrency(ccys.includes("INR") ? "INR" : ccys[0]);
    }).catch(() => {});
  }, [currency]);

  const loadCases = useCallback(() => {
    const params = {};
    if (search) params.q = search;
    if (statusFilter) params.status = statusFilter;
    api.get("/cases", { params }).then((res) => setCases(res.data.cases)).catch(() => setCases([]));
  }, [search, statusFilter]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => {
    const t = setTimeout(loadCases, 250);
    return () => clearTimeout(t);
  }, [loadCases]);

  if (!summary) {
    return (
      <div className="space-y-6" data-testid="dashboard-loading">
        <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-6">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  const { kpis, funnel, charts } = summary;
  const series = (charts.timeseries && currency && charts.timeseries[currency]) || [];
  const flatByAction = charts.recovery_by_action.map((r) => ({
    action: r.action.replace(/_/g, " "),
    amount: r.amounts[currency] ?? Object.values(r.amounts)[0] ?? 0,
  }));

  return (
    <div className="space-y-10" data-testid="dashboard">
      <div>
        <h1 className="font-heading text-3xl font-medium tracking-tight text-slate-900">Recovery Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          Revenue at risk, expected value and verified recovery — always reported separately, never blended.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-6">
        <KpiCard label="Revenue at Risk" accent="border-l-amber-500" testId="kpi-revenue-at-risk"
          sub={Object.keys(kpis.natural_recovered_not_counted).length > 0
            ? `Naturally recovered (not counted): ${Object.entries(kpis.natural_recovered_not_counted).map(([c, v]) => formatMoney(v, c)).join(", ")}`
            : "Currently unresolved eligible value"}>
          <MoneyMap amounts={kpis.revenue_at_risk} />
        </KpiCard>
        <KpiCard label="Verified Gross Recovery" accent="border-l-green-500" testId="kpi-verified-gross"
          sub="Only independently verified outcomes">
          <MoneyMap amounts={kpis.verified_gross_recovery} />
        </KpiCard>
        <KpiCard label="Verified Net Recovery" accent="border-l-green-600" testId="kpi-verified-net"
          sub={`Gross minus action costs (${Object.entries(kpis.action_costs).map(([c, v]) => formatMoney(v, c)).join(", ") || "0"}). A negative currency line means action costs exceeded verified recovery in that currency.`}>
          <MoneyMap amounts={kpis.verified_net_recovery} />
        </KpiCard>
        <KpiCard label="Active Cases" accent="border-l-blue-500" testId="kpi-active-cases"
          sub={`${kpis.outcomes_unknown} with unknown/unverified outcome`}>
          {kpis.active_cases}
        </KpiCard>
        <KpiCard label="Recovery Rate" accent="border-l-blue-600" testId="kpi-recovery-rate"
          sub="Verified recovered share of closed cases">
          {kpis.recovery_rate_pct}%
        </KpiCard>
        <KpiCard label="Exceptions" accent="border-l-red-500" testId="kpi-exceptions"
          sub="Records excluded from financial totals">
          {kpis.exceptions_open}
        </KpiCard>
      </div>

      <section data-testid="recovery-funnel" className="rounded-xl border border-slate-200 bg-white p-6">
        <h2 className="font-heading text-lg font-medium text-slate-900">Recovery Funnel</h2>
        <div className="mt-4 flex flex-wrap items-center gap-y-3">
          {FUNNEL_STAGES.map((stage, i) => (
            <div key={stage} className="flex items-center">
              <div className={`rounded-lg border px-4 py-2 text-center ${stage === "recovered" ? "border-green-200 bg-green-50" : "border-slate-200 bg-slate-50"}`}>
                <div className={`text-xl font-semibold tabular-nums ${stage === "recovered" ? "text-green-700" : "text-slate-900"}`}>
                  {funnel[stage] ?? 0}
                </div>
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">{stage}</div>
              </div>
              {i < FUNNEL_STAGES.length - 1 && <ChevronRight className="mx-1 h-4 w-4 text-slate-300" />}
            </div>
          ))}
        </div>
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="rounded-xl border border-slate-200 bg-white p-6 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="font-heading text-lg font-medium text-slate-900">At Risk vs Verified Recovery — 30 days</h2>
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
            {series.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#94A3B8" }} tickFormatter={(d) => d.slice(5)} />
                  <YAxis tick={{ fontSize: 11, fill: "#94A3B8" }} />
                  <Tooltip formatter={(value, name) => [formatMoney(value, currency), name === "at_risk" ? "Revenue at Risk (created)" : "Verified Recovered"]} labelFormatter={(d) => `Date: ${d}`} />
                  <Area type="monotone" dataKey="at_risk" stroke="#D97706" fill="#FEF3C7" strokeWidth={2} name="at_risk" />
                  <Area type="monotone" dataKey="verified_recovered" stroke="#16A34A" fill="#DCFCE7" strokeWidth={2} name="verified_recovered" />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyChart text="No time-series data yet. Ingest a file or send a simulated event." />
            )}
          </div>
        </div>

        <ChartCard title="Verified Recovery by Action" testId="chart-recovery-by-action">
          {flatByAction.length ? (
            <BarChart data={flatByAction}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis dataKey="action" tick={{ fontSize: 10, fill: "#94A3B8" }} />
              <YAxis tick={{ fontSize: 11, fill: "#94A3B8" }} />
              <Tooltip formatter={(v) => [formatMoney(v, currency), "Verified recovered"]} />
              <Bar dataKey="amount" fill="#16A34A" radius={[4, 4, 0, 0]} />
            </BarChart>
          ) : <EmptyChart text="No verified recoveries attributed to actions yet." />}
        </ChartCard>

        <ChartCard title="Cases by Status" testId="chart-cases-by-status">
          {charts.cases_by_status.length ? (
            <PieChart>
              <Pie data={charts.cases_by_status} dataKey="count" nameKey="status" innerRadius={50} outerRadius={80} paddingAngle={2}>
                {charts.cases_by_status.map((entry) => (
                  <Cell key={entry.status} fill={STATUS_COLORS[entry.status] || "#94A3B8"} />
                ))}
              </Pie>
              <Tooltip formatter={(v, name) => [v, name.replace(/_/g, " ")]} />
            </PieChart>
          ) : <EmptyChart text="No cases yet." />}
        </ChartCard>

        <ChartCard title="Failure Reasons" testId="chart-failure-reasons">
          {charts.failure_reasons.length ? (
            <BarChart data={charts.failure_reasons} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis type="number" tick={{ fontSize: 11, fill: "#94A3B8" }} allowDecimals={false} />
              <YAxis type="category" dataKey="code" width={150} tick={{ fontSize: 10, fill: "#64748B" }} />
              <Tooltip />
              <Bar dataKey="count" fill="#2563EB" radius={[0, 4, 4, 0]} />
            </BarChart>
          ) : <EmptyChart text="No failed payments recorded yet." />}
        </ChartCard>

        <ChartCard title="Policy Control Activity" testId="chart-policy-blocks">
          <div className="flex h-full flex-col justify-center gap-4 overflow-auto">
            <div className="flex flex-wrap gap-3">
              {charts.policy_decisions.length ? charts.policy_decisions.map((d) => (
                <div key={d.decision} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2">
                  <StatusBadge value={d.decision} />
                  <span className="text-lg font-semibold tabular-nums text-slate-900">{d.count}</span>
                </div>
              )) : <span className="text-sm text-slate-400">No policy decisions recorded yet.</span>}
            </div>
            {charts.policy_block_rules.length > 0 && (
              <div>
                <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500 mb-2">Block / stop rules triggered</div>
                <ul className="space-y-1">
                  {charts.policy_block_rules.map((r) => (
                    <li key={r.rule} className="flex justify-between text-sm text-slate-600">
                      <span className="font-mono text-xs">{r.rule}</span>
                      <span className="tabular-nums font-medium">{r.count}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </ChartCard>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white" data-testid="cases-section">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 p-6">
          <h2 className="font-heading text-lg font-medium text-slate-900">Recovery Cases</h2>
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
              <input
                data-testid="case-search-input"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search case, order, payment…"
                className="w-full sm:w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 outline-none transition-colors duration-200 focus:border-slate-400"
              />
            </div>
            <select
              data-testid="case-status-filter"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
            >
              <option value="">All statuses</option>
              {["OPEN", "EVALUATED", "APPROVAL_PENDING", "ACTION_EXECUTED", "VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "STOPPED", "INVALID"].map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="cases-table">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                <th className="px-6 py-3">Case</th>
                <th className="px-6 py-3 text-right">Amount at Risk</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Recommended Action</th>
                <th className="px-6 py-3">Policy</th>
                <th className="px-6 py-3 text-right">Confidence</th>
                <th className="px-6 py-3">Outcome</th>
                <th className="px-6 py-3">Source</th>
              </tr>
            </thead>
            <tbody>
              {(cases || []).map((c) => (
                <tr
                  key={c.case_id}
                  data-testid={`case-row-${c.case_id}`}
                  onClick={() => navigate(`/cases/${c.case_id}`)}
                  className="cursor-pointer border-b border-slate-100 transition-colors duration-200 hover:bg-slate-50"
                >
                  <td className="px-6 py-3">
                    <div className="font-mono text-xs font-medium text-slate-900">{c.case_id}</div>
                    <div className="font-mono text-[11px] text-slate-400">{c.order_key}</div>
                  </td>
                  <td className="px-6 py-3 text-right"><Money amount={c.amount_at_risk} currency={c.currency} className="text-slate-900" /></td>
                  <td className="px-6 py-3"><StatusBadge value={c.status} /></td>
                  <td className="px-6 py-3 text-slate-600">{(c.recommended_action || "—").replace(/_/g, " ")}</td>
                  <td className="px-6 py-3"><StatusBadge value={c.policy_result?.decision} /></td>
                  <td className="px-6 py-3 text-right tabular-nums text-slate-600">{c.confidence != null ? `${Math.round(c.confidence * 100)}%` : "—"}</td>
                  <td className="px-6 py-3"><StatusBadge value={c.outcome} /></td>
                  <td className="px-6 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs text-slate-500">{c.source}</span>
                      {c.simulated && <StatusBadge value="SIMULATED" />}
                    </div>
                  </td>
                </tr>
              ))}
              {cases && cases.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-6 py-12 text-center text-sm text-slate-400">
                    No recovery cases match. Ingest merchant data or send a simulated payment event to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function ChartCard({ title, testId, children }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6" data-testid={testId}>
      <h3 className="font-heading text-base font-medium text-slate-900">{title}</h3>
      <div className="mt-4 h-56">
        <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
      </div>
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
