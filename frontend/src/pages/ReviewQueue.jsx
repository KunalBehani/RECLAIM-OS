import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";
import PageHeader from "../components/PageHeader";
import EmptyState from "../components/EmptyState";
import ErrorState from "../components/ErrorState";
import { Money } from "../components/Money";

const PAGE_SIZE = 25;

function aiEstimate(c) {
  if (c.confidence != null && c.confidence_type !== "heuristic" && c.model_version !== "heuristic-fallback-v1") {
    return `est. ${Math.round(c.confidence * 100)}% — uncalibrated`;
  }
  if (c.confidence_type === "heuristic" || c.model_version === "heuristic-fallback-v1") return "heuristic assessment";
  return null;
}

export default function ReviewQueue() {
  const navigate = useNavigate();
  const [queue, setQueue] = useState(null);
  const [page, setPage] = useState(1);

  const load = useCallback(() => {
    api.get("/review/queue").then((res) => { setQueue(res.data); setPage(1); }).catch(() => setQueue({ error: true }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const resolveException = async (exceptionId) => {
    await api.post(`/exceptions/${exceptionId}/resolve`);
    toast.success("Exception resolved");
    load();
  };

  if (!queue) {
    return <div className="h-64 animate-pulse rounded-xl bg-slate-200" data-testid="review-loading" />;
  }

  const header = (
    <PageHeader eyebrow="Recovery" title="Human Review Queue"
      subtitle="AI recommends — humans approve. A human approval can execute an APPROVAL-gated action, but can never override a policy BLOCK or STOP." />
  );

  if (queue.error) {
    return (
      <div className="space-y-10" data-testid="review-queue">
        {header}
        <ErrorState testId="review-error" title="Review queue unavailable"
          detail="The queue could not be loaded. No data has been changed." onRetry={load} />
      </div>
    );
  }

  const pending = queue.approval_pending;
  const total = pending.length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const rows = pending.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const from = total === 0 ? 0 : (current - 1) * PAGE_SIZE + 1;
  const to = Math.min(current * PAGE_SIZE, total);

  return (
    <div className="space-y-10" data-testid="review-queue">
      {header}

      <section className="space-y-4">
        <h2 className="font-heading text-lg font-medium text-slate-900">
          Awaiting approval <span className="ml-2 rounded-full bg-orange-50 px-2.5 py-0.5 text-xs font-bold text-orange-700 border border-orange-200">{queue.counts.approval_pending}</span>
        </h2>
        {total === 0 ? (
          <EmptyState testId="review-empty" title="No cases awaiting human approval"
            hint="When the policy engine requests human review, cases appear here." />
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="overflow-x-auto">
              <table className="w-full text-sm" data-testid="review-table">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                    <th className="px-5 py-3">Case</th>
                    <th className="px-5 py-3 text-right">Amount at risk</th>
                    <th className="px-5 py-3">AI recommendation</th>
                    <th className="px-5 py-3">Policy</th>
                    <th className="px-5 py-3">Status</th>
                    <th className="px-5 py-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c) => {
                    const estimate = aiEstimate(c);
                    const reasons = c.policy_result?.reasons || [];
                    return (
                      <tr key={c.case_id} className="border-b border-slate-100 transition-colors duration-150 hover:bg-slate-50"
                        data-testid={`review-case-${c.case_id}`}>
                        <td className="px-5 py-3.5">
                          <button onClick={() => navigate(`/cases/${c.case_id}`)}
                            className="text-left text-sm font-semibold text-slate-900 hover:underline">
                            {c.title || c.case_id}
                          </button>
                          <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[11px] text-slate-400">
                            <span>{c.case_id}</span>
                            <span>· {c.order_key}</span>
                            {c.simulated && <StatusBadge value="SIMULATED" />}
                          </div>
                        </td>
                        <td className="px-5 py-3.5 text-right">
                          <Money amount={c.amount_at_risk} currency={c.currency} className="text-sm font-semibold tabular-nums text-amber-700" />
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="text-sm font-medium text-slate-800">{(c.recommended_action || "—").replace(/_/g, " ")}</div>
                          {estimate && <div className="mt-0.5 text-[11px] text-slate-400">{estimate}</div>}
                        </td>
                        <td className="px-5 py-3.5">
                          {reasons.length > 0 ? (
                            <span className="text-xs text-orange-700" title={reasons.map((r) => `${r.rule} — ${r.detail}`).join("\n")}>
                              <span className="font-mono">{reasons[0].rule}</span>
                              {reasons.length > 1 && <span className="text-slate-400"> +{reasons.length - 1} more</span>}
                            </span>
                          ) : (
                            <span className="text-xs text-slate-400">Checks passed</span>
                          )}
                        </td>
                        <td className="px-5 py-3.5"><StatusBadge value={c.status} /></td>
                        <td className="px-5 py-3.5 text-right">
                          <button data-testid={`review-open-${c.case_id}`} onClick={() => navigate(`/cases/${c.case_id}`)}
                            className="rounded-lg bg-slate-900 px-4 py-2 text-xs font-medium text-white transition-colors duration-200 hover:bg-slate-800">
                            Review
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3" data-testid="review-pagination">
              <span className="text-xs text-slate-500 tabular-nums" data-testid="review-pagination-info">
                Showing {from}–{to} of {total}
              </span>
              <div className="flex items-center gap-1">
                <button data-testid="review-prev-page" disabled={current <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
                  aria-label="Previous page"
                  className="rounded-md border border-slate-200 p-1.5 text-slate-600 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-40">
                  <ChevronLeft className="h-4 w-4" />
                </button>
                {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
                  <button key={p} data-testid={`review-page-${p}`} onClick={() => setPage(p)}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium tabular-nums transition-colors duration-200 ${p === current ? "bg-slate-900 text-white" : "border border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                    {p}
                  </button>
                ))}
                <button data-testid="review-next-page" disabled={current >= pageCount} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                  aria-label="Next page"
                  className="rounded-md border border-slate-200 p-1.5 text-slate-600 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-40">
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 className="font-heading text-lg font-medium text-slate-900">
          Exception queue <span className="ml-2 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-bold text-red-700 border border-red-200">{queue.counts.exceptions}</span>
        </h2>
        <p className="mt-1 text-xs text-slate-500">Records that failed validation. They are excluded from every financial total until resolved.</p>
        <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="w-full text-sm" data-testid="exceptions-table">
            <thead>
              <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                <th className="px-6 py-3">Record</th>
                <th className="px-6 py-3">Reason</th>
                <th className="px-6 py-3">Source</th>
                <th className="px-6 py-3">Received</th>
                <th className="px-6 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {queue.exceptions.map((e) => (
                <tr key={e.exception_id} className="border-b border-slate-100">
                  <td className="px-6 py-3 font-mono text-xs text-slate-700">{e.record_ref || e.exception_id}</td>
                  <td className="px-6 py-3 font-mono text-xs text-red-600">{e.reason}</td>
                  <td className="px-6 py-3 text-xs text-slate-500">{e.source}</td>
                  <td className="px-6 py-3 text-xs text-slate-500">{new Date(e.created_at).toLocaleString("en-GB")}</td>
                  <td className="px-6 py-3 text-right">
                    <button data-testid={`resolve-exception-${e.exception_id}`} onClick={() => resolveException(e.exception_id)}
                      className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 transition-colors duration-200 hover:bg-slate-50">
                      Resolve
                    </button>
                  </td>
                </tr>
              ))}
              {queue.exceptions.length === 0 && (
                <tr><td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-400">Exception queue is empty.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
