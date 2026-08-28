import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, CheckCircle2, Circle, Clock, Pause, Play, ShieldCheck,
  SkipBack, SkipForward, XCircle,
} from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";
import { Money, formatMoney } from "../components/Money";

const ACTION_OPTIONS = ["SCHEDULED_RECHECK", "SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER", "ESCALATE_HUMAN"];

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

export default function CaseDetail() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [replay, setReplay] = useState(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [actionChoice, setActionChoice] = useState("SCHEDULED_RECHECK");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [detail, replayRes] = await Promise.all([
        api.get(`/cases/${caseId}`),
        api.get(`/cases/${caseId}/replay`),
      ]);
      setData(detail.data);
      setReplay(replayRes.data);
    } catch {
      toast.error("Case not found");
      navigate("/");
    }
  }, [caseId, navigate]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!playing || !replay) return undefined;
    if (stepIdx >= replay.steps.length - 1) { setPlaying(false); return undefined; }
    const t = setTimeout(() => setStepIdx((i) => Math.min(i + 1, replay.steps.length - 1)), 1400);
    return () => clearTimeout(t);
  }, [playing, stepIdx, replay]);

  const run = async (fn, successMsg) => {
    setBusy(true);
    try {
      const res = await fn();
      if (successMsg) toast.success(successMsg);
      await load();
      return res;
    } catch (e) {
      toast.error(e.response?.data?.detail || "Action failed");
      return null;
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return <div className="space-y-6" data-testid="case-loading"><div className="h-40 animate-pulse rounded-xl bg-slate-200" /><div className="h-96 animate-pulse rounded-xl bg-slate-200" /></div>;
  }

  const { case: c, attempts, actions, audit_trail: audit } = data;
  const policy = c.policy_result || {};
  const executedActions = actions.filter((a) => a.executed_time);

  return (
    <div className="space-y-10" data-testid="case-detail">
      <div>
        <button onClick={() => navigate("/")} data-testid="back-to-dashboard-btn"
          className="mb-4 flex items-center gap-1.5 text-sm text-slate-500 transition-colors duration-200 hover:text-slate-900">
          <ArrowLeft className="h-4 w-4" /> Back to dashboard
        </button>
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="font-heading font-mono text-2xl font-medium tracking-tight text-slate-900">{c.case_id}</h1>
          <StatusBadge value={c.status} />
          <StatusBadge value={c.verification_status} />
          {c.simulated && <StatusBadge value="SIMULATED" />}
        </div>
        <p className="mt-2 max-w-3xl text-sm text-slate-500">{c.reason_created}</p>
      </div>

      {replay && replay.steps.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="decision-replay">
          <div className="flex items-center justify-between">
            <h2 className="font-heading text-lg font-medium text-slate-900">Decision Replay</h2>
            <div className="flex items-center gap-2">
              <button data-testid="decision-replay-prev-btn" onClick={() => { setPlaying(false); setStepIdx((i) => Math.max(0, i - 1)); }}
                className="rounded-lg border border-slate-200 p-2 text-slate-600 transition-colors duration-200 hover:bg-slate-50">
                <SkipBack className="h-4 w-4" />
              </button>
              <button data-testid="decision-replay-play-btn" onClick={() => { if (stepIdx >= replay.steps.length - 1) setStepIdx(0); setPlaying(!playing); }}
                className="rounded-lg bg-slate-900 p-2 text-white transition-colors duration-200 hover:bg-slate-800">
                {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              </button>
              <button data-testid="decision-replay-next-btn" onClick={() => { setPlaying(false); setStepIdx((i) => Math.min(replay.steps.length - 1, i + 1)); }}
                className="rounded-lg border border-slate-200 p-2 text-slate-600 transition-colors duration-200 hover:bg-slate-50">
                <SkipForward className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="mt-6 flex gap-2 overflow-x-auto pb-2">
            {replay.steps.map((step, i) => (
              <button
                key={step.event_id}
                data-testid={`replay-step-${i}`}
                onClick={() => { setPlaying(false); setStepIdx(i); }}
                className={`min-w-[130px] rounded-lg border p-3 text-left transition-all duration-300 ${
                  i === stepIdx
                    ? "scale-105 border-slate-900 bg-slate-900 text-white shadow-md"
                    : i < stepIdx
                      ? "border-slate-200 bg-slate-50 text-slate-400"
                      : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                }`}
              >
                <div className="text-[10px] font-bold uppercase tracking-wider opacity-70">{step.stage}</div>
                <div className="mt-1 text-xs font-medium leading-tight">{step.event_type.replace(/_/g, " ")}</div>
                <div className="mt-1 font-mono text-[10px] opacity-60">{fmtTime(step.timestamp)}</div>
              </button>
            ))}
          </div>
          {replay.steps[stepIdx] && (
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-5" data-testid="replay-detail">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-xs font-semibold text-slate-900">{replay.steps[stepIdx].event_type}</span>
                <span className="text-xs text-slate-500">actor: {replay.steps[stepIdx].actor}</span>
                {replay.steps[stepIdx].model_version && (
                  <span className="rounded bg-blue-50 px-2 py-0.5 font-mono text-[10px] text-blue-700 border border-blue-200">model: {replay.steps[stepIdx].model_version}</span>
                )}
                {replay.steps[stepIdx].policy_rule_reference && (
                  <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-600 border border-slate-200">{replay.steps[stepIdx].policy_rule_reference}</span>
                )}
              </div>
              <p className="mt-3 text-sm leading-relaxed text-slate-700">{replay.steps[stepIdx].reason || "—"}</p>
              {(replay.steps[stepIdx].before_state || replay.steps[stepIdx].after_state) && (
                <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                  {replay.steps[stepIdx].before_state && (
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Before</div>
                      <pre className="mt-1 overflow-auto rounded-md bg-white p-3 font-mono text-[11px] text-slate-600 border border-slate-200">{JSON.stringify(replay.steps[stepIdx].before_state, null, 2)}</pre>
                    </div>
                  )}
                  {replay.steps[stepIdx].after_state && (
                    <div>
                      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">After</div>
                      <pre className="mt-1 overflow-auto rounded-md bg-white p-3 font-mono text-[11px] text-slate-600 border border-slate-200">{JSON.stringify(replay.steps[stepIdx].after_state, null, 2)}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="ai-analysis">
            <div className="flex items-center justify-between">
              <h2 className="font-heading text-lg font-medium text-slate-900">AI Analysis</h2>
              {c.model_version && (
                <span className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 font-mono text-[10px] text-blue-700" data-testid="model-version">
                  {c.model_version}
                </span>
              )}
            </div>
            <p className="mt-3 text-sm leading-relaxed text-slate-700">{c.diagnosis || "Pending analysis."}</p>
            <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="rounded-lg bg-slate-50 p-4 border border-slate-100">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Natural recovery baseline</div>
                <div className="mt-1 text-xl font-semibold tabular-nums text-slate-900">
                  {c.natural_recovery_probability != null ? `${Math.round(c.natural_recovery_probability * 100)}%` : "—"}
                </div>
                <div className="text-xs text-slate-500">expected: {formatMoney(c.expected_natural_recovery_value, c.currency)}</div>
              </div>
              <div className="rounded-lg bg-slate-50 p-4 border border-slate-100">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Confidence</div>
                <div className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{c.confidence != null ? `${Math.round(c.confidence * 100)}%` : "—"}</div>
              </div>
              <div className="rounded-lg bg-slate-50 p-4 border border-slate-100">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Recommended</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">{(c.recommended_action || "—").replace(/_/g, " ")}</div>
              </div>
            </div>
            {c.explanation && <p className="mt-4 text-sm leading-relaxed text-slate-600 border-l-2 border-blue-200 pl-4">{c.explanation}</p>}
            {c.selection_reason && <p className="mt-2 text-xs text-slate-500">{c.selection_reason}</p>}
            {c.evidence?.length > 0 && (
              <ul className="mt-3 space-y-1">
                {c.evidence.map((e, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-slate-500">
                    <Circle className="mt-1 h-2 w-2 fill-slate-300 text-slate-300" />{e}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="action-comparison">
            <h2 className="font-heading text-lg font-medium text-slate-900">Action Comparison — Expected Incremental Value</h2>
            <p className="mt-1 text-xs text-slate-500">EIV = amount × (P(recovery | action) − P(natural recovery)) − action cost. The engine optimizes net incremental value, not action volume.</p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                    <th className="py-2 pr-4">Action</th>
                    <th className="py-2 pr-4 text-right">P(recovery)</th>
                    <th className="py-2 pr-4 text-right">Uplift</th>
                    <th className="py-2 pr-4 text-right">Cost</th>
                    <th className="py-2 text-right">EIV</th>
                  </tr>
                </thead>
                <tbody>
                  {(c.action_evaluations || []).map((e) => (
                    <tr key={e.action_type} className={`border-b border-slate-100 ${e.action_type === c.recommended_action ? "bg-blue-50/60" : ""}`}>
                      <td className="py-2.5 pr-4">
                        <span className="font-medium text-slate-800">{e.label}</span>
                        {e.action_type === c.recommended_action && (
                          <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-700">SELECTED</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{Math.round(e.p_recovery * 100)}%</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{e.uplift > 0 ? "+" : ""}{Math.round(e.uplift * 100)}%</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{formatMoney(e.estimated_cost, c.currency)}</td>
                      <td className={`py-2.5 text-right tabular-nums font-medium ${e.expected_incremental_value > 0 ? "text-green-700" : "text-slate-400"}`}>
                        {formatMoney(e.expected_incremental_value, c.currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="audit-trail">
            <h2 className="font-heading text-lg font-medium text-slate-900">Audit Trail</h2>
            <div className="mt-4 space-y-0">
              {audit.map((event, i) => (
                <div key={event.event_id} className="relative flex gap-4 pb-5">
                  {i < audit.length - 1 && <div className="absolute left-[7px] top-5 h-full w-px bg-slate-200" />}
                  <div className="relative mt-1.5 h-3.5 w-3.5 shrink-0 rounded-full border-2 border-slate-300 bg-white" />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-slate-900">{event.event_type}</span>
                      <span className="font-mono text-[11px] text-slate-400">{fmtTime(event.timestamp)}</span>
                      <span className="text-[11px] text-slate-400">· {event.actor}</span>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">{event.reason}</p>
                  </div>
                </div>
              ))}
              {audit.length === 0 && <p className="text-sm text-slate-400">No audit events recorded.</p>}
            </div>
          </section>
        </div>

        <div className="space-y-6">
          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="case-summary">
            <h3 className="font-heading text-base font-medium text-slate-900">Summary</h3>
            <dl className="mt-4 space-y-3 text-sm">
              <Row label="Amount at risk" value={<Money amount={c.amount_at_risk} currency={c.currency} className="text-base font-semibold text-amber-700" />} />
              <Row label="Order" value={<span className="font-mono text-xs">{c.order_key}</span>} />
              <Row label="Customer" value={<span className="font-mono text-xs">{c.customer_reference || "—"}</span>} />
              <Row label="Source" value={<span className="flex items-center gap-1.5">{c.source} {c.simulated && <StatusBadge value="SIMULATED" />}</span>} />
              <Row label="Created" value={<span className="font-mono text-xs">{fmtTime(c.created_at)}</span>} />
              <Row label="Outcome" value={<StatusBadge value={c.outcome} />} />
              {c.status === "VERIFIED_RECOVERED" && (
                <Row label="Verified recovered" value={<Money amount={c.recovered_amount} currency={c.currency} className="font-semibold text-green-700" />} />
              )}
              {c.attributed_action && <Row label="Attributed to" value={<span className="text-xs font-medium">{c.attributed_action.replace(/_/g, " ")}</span>} />}
            </dl>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="policy-result">
            <h3 className="font-heading text-base font-medium text-slate-900">Policy Result</h3>
            <div className="mt-3 flex items-center gap-2">
              <StatusBadge value={policy.decision} />
              {policy.rule_version && <span className="font-mono text-[10px] text-slate-400">{policy.rule_version}</span>}
            </div>
            <ul className="mt-3 space-y-2">
              {(policy.reasons || []).map((r, i) => (
                <li key={i} className="rounded-lg border border-slate-100 bg-slate-50 p-2.5">
                  <div className="font-mono text-[10px] font-semibold text-slate-500">{r.rule}</div>
                  <div className="mt-0.5 text-xs text-slate-600">{r.detail}</div>
                </li>
              ))}
              {(!policy.reasons || policy.reasons.length === 0) && policy.decision && (
                <li className="text-xs text-slate-500">All deterministic policy checks passed.</li>
              )}
              {!policy.decision && <li className="text-xs text-slate-400">No policy evaluation yet.</li>}
            </ul>
            <p className="mt-3 text-[11px] text-slate-400">The policy engine is deterministic and cannot be overridden by the AI — only by a human, and never against a BLOCK/STOP.</p>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="payment-timeline">
            <h3 className="font-heading text-base font-medium text-slate-900">Payment Timeline</h3>
            <div className="mt-4 space-y-3">
              {attempts.map((a) => (
                <div key={a.payment_id} className="flex items-start gap-3">
                  {a.status === "success" ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                  ) : a.status === "failed" ? (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                  ) : (
                    <Clock className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs text-slate-700">{a.payment_id}</span>
                      <Money amount={a.amount} currency={a.currency} className="text-xs text-slate-900" />
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                      <span>{fmtTime(a.timestamp)}</span>
                      <span>· {a.source}</span>
                      {a.simulated && <StatusBadge value="SIMULATED" />}
                      {a.failure_code && <span className="font-mono text-red-500">{a.failure_code}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="execution-history">
            <h3 className="font-heading text-base font-medium text-slate-900">Execution History</h3>
            <div className="mt-3 space-y-3">
              {executedActions.map((a) => (
                <div key={a.action_id} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-800">{a.label}</span>
                    <StatusBadge value={a.execution_mode} />
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-slate-400">
                    {fmtTime(a.executed_time)} · ref {a.provider_reference} · {a.approval_status.replace(/_/g, " ")}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    outcome: <span className="font-medium">{a.outcome}</span> · cost {formatMoney(a.estimated_cost, c.currency)}
                  </div>
                </div>
              ))}
              {executedActions.length === 0 && <p className="text-sm text-slate-400">No actions executed.</p>}
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="verification-card">
            <h3 className="font-heading text-base font-medium text-slate-900">Outcome Verification</h3>
            <div className="mt-3"><StatusBadge value={c.verification_status} /></div>
            {c.verification_evidence ? (
              <div className="mt-3 rounded-lg border border-green-100 bg-green-50 p-3 text-xs text-green-800">
                <ShieldCheck className="mb-1 h-4 w-4" />
                Verified via source-of-truth payment <span className="font-mono">{c.verification_evidence.success_payment_id}</span> ({c.verification_evidence.source}) at {fmtTime(c.verification_evidence.success_timestamp)}.
              </div>
            ) : (
              <p className="mt-3 text-xs text-slate-500">No verified settlement yet. Executed actions do NOT count as recovery until a successful payment is independently observed.</p>
            )}
          </section>

          <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="case-actions">
            <h3 className="font-heading text-base font-medium text-slate-900">Controls</h3>
            <div className="mt-4 space-y-3">
              <button data-testid="verify-now-btn" disabled={busy}
                onClick={() => run(() => api.post(`/cases/${c.case_id}/verify`), "Verification sweep complete")}
                className="w-full rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                Run verification sweep
              </button>
              <button data-testid="reevaluate-btn" disabled={busy}
                onClick={() => run(() => api.post(`/cases/${c.case_id}/evaluate`), "Case re-evaluated")}
                className="w-full rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                Re-run AI analysis + policy
              </button>
              <div className="flex gap-2">
                <select data-testid="manual-action-select" value={actionChoice} onChange={(e) => setActionChoice(e.target.value)}
                  className="flex-1 rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-700 outline-none">
                  {ACTION_OPTIONS.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
                </select>
                <button data-testid="manual-execute-btn" disabled={busy}
                  onClick={() => run(() => api.post(`/cases/${c.case_id}/execute`, { action_type: actionChoice }), "Policy check complete")}
                  className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition-colors duration-200 hover:bg-blue-700 disabled:opacity-50">
                  Execute
                </button>
              </div>
              {c.status === "APPROVAL_PENDING" && (
                <div className="grid grid-cols-2 gap-2 border-t border-slate-100 pt-3">
                  <button data-testid="approve-case-btn" disabled={busy}
                    onClick={() => run(() => api.post(`/cases/${c.case_id}/review`, { decision: "approve" }), "Approved — executed if policy allows")}
                    className="rounded-lg bg-green-600 px-3 py-2 text-xs font-medium text-white transition-colors duration-200 hover:bg-green-700 disabled:opacity-50">
                    Approve
                  </button>
                  <button data-testid="reject-case-btn" disabled={busy}
                    onClick={() => run(() => api.post(`/cases/${c.case_id}/review`, { decision: "reject" }), "Recommendation rejected")}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                    Reject
                  </button>
                  <button data-testid="invalid-case-btn" disabled={busy}
                    onClick={() => { if (window.confirm("Mark this case invalid? It will be removed from revenue-at-risk totals.")) run(() => api.post(`/cases/${c.case_id}/review`, { decision: "invalid" }), "Case marked invalid"); }}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-50">
                    Mark invalid
                  </button>
                  <button data-testid="stop-case-btn" disabled={busy}
                    onClick={() => { if (window.confirm("Stop all recovery for this case?")) run(() => api.post(`/cases/${c.case_id}/review`, { decision: "stop" }), "Recovery stopped"); }}
                    className="rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white transition-colors duration-200 hover:bg-red-700 disabled:opacity-50">
                    Stop recovery
                  </button>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right">{value}</dd>
    </div>
  );
}
