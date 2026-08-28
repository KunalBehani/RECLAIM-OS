import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";
import { Money, formatMoney } from "../components/Money";

const ALTERNATE_ACTIONS = ["SCHEDULED_RECHECK", "SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER", "ESCALATE_HUMAN", "STOP_RECOVERY"];

export default function ReviewQueue() {
  const navigate = useNavigate();
  const [queue, setQueue] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/review/queue").then((res) => setQueue(res.data)).catch(() => setQueue({ approval_pending: [], exceptions: [], counts: {} }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const decide = async (caseId, decision, actionType) => {
    if ((decision === "invalid" || decision === "stop") && !window.confirm(`Confirm: ${decision === "invalid" ? "mark this case invalid" : "stop recovery"}?`)) return;
    setBusy(true);
    try {
      const res = await api.post(`/cases/${caseId}/review`, { decision, action_type: actionType });
      if (res.data?.policy_result && res.data.executed === false && (decision === "approve" || decision === "alternate")) {
        toast.warning(`Policy engine overrode the approval: ${res.data.policy_result.decision}`);
      } else {
        toast.success(`Decision recorded: ${decision}`);
      }
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Decision failed");
    } finally {
      setBusy(false);
    }
  };

  const resolveException = async (exceptionId) => {
    await api.post(`/exceptions/${exceptionId}/resolve`);
    toast.success("Exception resolved");
    load();
  };

  if (!queue) {
    return <div className="h-64 animate-pulse rounded-xl bg-slate-200" data-testid="review-loading" />;
  }

  return (
    <div className="space-y-10" data-testid="review-queue">
      <div>
        <h1 className="font-heading text-3xl font-medium tracking-tight text-slate-900">Human Review Queue</h1>
        <p className="mt-1 text-sm text-slate-500">
          AI recommends — humans approve. A human approval can execute an APPROVAL-gated action, but can never override a policy BLOCK or STOP.
        </p>
      </div>

      <section className="space-y-4">
        <h2 className="font-heading text-lg font-medium text-slate-900">
          Awaiting approval <span className="ml-2 rounded-full bg-orange-50 px-2.5 py-0.5 text-xs font-bold text-orange-700 border border-orange-200">{queue.counts.approval_pending}</span>
        </h2>
        {queue.approval_pending.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400" data-testid="review-empty">
            No cases awaiting human approval.
          </div>
        )}
        {queue.approval_pending.map((c) => (
          <div key={c.case_id} className="rounded-xl border border-orange-200 bg-white p-6" data-testid={`review-case-${c.case_id}`}>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-3">
                  <button onClick={() => navigate(`/cases/${c.case_id}`)} className="text-left text-sm font-semibold text-slate-900 hover:underline" data-testid={`review-open-${c.case_id}`}>
                    {c.title || c.case_id}
                  </button>
                  <StatusBadge value={c.status} />
                  {c.simulated && <StatusBadge value="SIMULATED" />}
                </div>
                <div className="mt-1 font-mono text-[11px] text-slate-400">{c.case_id} · {c.order_key}</div>
                <div className="mt-2 text-sm text-slate-600">
                  <Money amount={c.amount_at_risk} currency={c.currency} className="text-base font-semibold text-amber-700" />
                  <span className="ml-3 text-xs text-slate-400">order {c.order_key}</span>
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  AI recommended: <span className="font-medium text-slate-700">{(c.recommended_action || "—").replace(/_/g, " ")}</span>
                  {c.confidence != null && c.confidence_type !== "heuristic" && c.model_version !== "heuristic-fallback-v1" && (
                    <span className="ml-2">model estimate {Math.round(c.confidence * 100)}% (uncalibrated)</span>
                  )}
                  {(c.confidence_type === "heuristic" || c.model_version === "heuristic-fallback-v1") && (
                    <span className="ml-2">heuristic assessment</span>
                  )}
                  {c.model_version && <span className="ml-2 font-mono text-[10px] text-slate-400">{c.model_version}</span>}
                </div>
                {c.policy_result?.reasons?.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {c.policy_result.reasons.map((r, i) => (
                      <li key={i} className="text-xs text-orange-700"><span className="font-mono">{r.rule}</span> — {r.detail}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <button data-testid={`approve-case-btn-${c.case_id}`} disabled={busy} onClick={() => decide(c.case_id, "approve")}
                    className="rounded-lg bg-green-600 px-4 py-2 text-xs font-medium text-white transition-colors duration-200 hover:bg-green-700 disabled:opacity-50">
                    Approve & execute
                  </button>
                  <button data-testid={`reject-case-btn-${c.case_id}`} disabled={busy} onClick={() => decide(c.case_id, "reject")}
                    className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                    Reject
                  </button>
                </div>
                <AlternateAction caseId={c.case_id} currency={c.currency} busy={busy} onDecide={decide} />
                <div className="flex gap-2">
                  <button data-testid={`invalid-case-btn-${c.case_id}`} disabled={busy} onClick={() => decide(c.case_id, "invalid")}
                    className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[11px] text-slate-600 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                    Mark invalid
                  </button>
                  <button data-testid={`stop-case-btn-${c.case_id}`} disabled={busy} onClick={() => decide(c.case_id, "stop")}
                    className="flex-1 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] font-medium text-red-700 transition-colors duration-200 hover:bg-red-100 disabled:opacity-50">
                    Stop recovery
                  </button>
                </div>
              </div>
            </div>
          </div>
        ))}
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

function AlternateAction({ caseId, currency, busy, onDecide }) {
  const [choice, setChoice] = useState("CUSTOMER_REMINDER");
  return (
    <div className="flex gap-2">
      <select data-testid={`alternate-action-select-${caseId}`} value={choice} onChange={(e) => setChoice(e.target.value)}
        className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-700 outline-none">
        {ALTERNATE_ACTIONS.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
      </select>
      <button data-testid={`alternate-action-btn-${caseId}`} disabled={busy} onClick={() => onDecide(caseId, "alternate", choice)}
        className="rounded-lg bg-blue-600 px-3 py-1.5 text-[11px] font-medium text-white transition-colors duration-200 hover:bg-blue-700 disabled:opacity-50">
        Execute alternate
      </button>
    </div>
  );
}
