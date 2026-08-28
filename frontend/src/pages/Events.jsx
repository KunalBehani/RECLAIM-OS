import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FlaskConical, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";

const SCENARIOS = [
  { id: 1, title: "Natural recovery", desc: "Payment fails → customer later pays on their own → no intervention" },
  { id: 2, title: "Full recovery loop", desc: "Fail → AI selects action → policy allows → executed (SIMULATED) → verified recovered" },
  { id: 3, title: "High-value approval", desc: "Amount above threshold → human approval required" },
  { id: 4, title: "Retry limit", desc: "3 retries already done → policy blocks the 4th" },
  { id: 5, title: "Duplicate webhook", desc: "Same event_id replayed → blocked as duplicate" },
  { id: 6, title: "Unknown outcome", desc: "Action executed → no settlement found → $0 counted as recovered" },
];

const FAILURE_CODES = ["insufficient_funds", "do_not_honor", "try_again_later", "issuer_unavailable", "card_declined_permanent", "stolen_card", "expired_card", "processing_error"];

export default function Events() {
  const navigate = useNavigate();
  const [config, setConfig] = useState(null);
  const [events, setEvents] = useState(null);
  const [form, setForm] = useState({ order_id: "", amount: "2500", currency: "INR", status: "failed", failure_code: "insufficient_funds" });
  const [eventResult, setEventResult] = useState(null);
  const [scenarioResult, setScenarioResult] = useState(null);
  const [sigResult, setSigResult] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get("/webhooks/config").then((res) => setConfig(res.data)).catch(() => {});
    api.get("/webhooks/events").then((res) => setEvents(res.data)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const sendEvent = async () => {
    setBusy(true);
    setEventResult(null);
    try {
      const payload = { ...form, amount: parseFloat(form.amount) || 0 };
      if (!payload.order_id) delete payload.order_id;
      const res = await api.post("/simulate/payment-event", payload);
      setEventResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Event failed");
    } finally {
      setBusy(false);
    }
  };

  const runScenario = async (id) => {
    setBusy(true);
    setScenarioResult(null);
    try {
      const res = await api.post(`/simulate/scenario/${id}`);
      setScenarioResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Scenario failed");
    } finally {
      setBusy(false);
    }
  };

  const testInvalidSignature = async () => {
    setSigResult(null);
    try {
      const res = await api.post("/simulate/invalid-signature-test");
      setSigResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Invalid-signature test failed");
    }
  };

  return (
    <div className="space-y-10" data-testid="events-page">
      <div>
        <h1 className="font-heading text-3xl font-medium tracking-tight text-slate-900">Events & Simulator</h1>
        <p className="mt-1 text-sm text-slate-500">
          Real-time ingestion with signature verification, idempotency and replay protection — plus a clearly labeled simulator for development and demos.
        </p>
      </div>

      {config && (
        <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="webhook-config">
          <h2 className="font-heading text-lg font-medium text-slate-900">Webhook Endpoint</h2>
          <div className="mt-3 space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-slate-500">POST</span>
              <code className="max-w-full overflow-x-auto break-all rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-800">{window.location.origin}{config.endpoint_path}</code>
              <StatusBadge value="TEST_MODE" />
            </div>
            <p className="text-xs text-slate-500">
              Signature: <code className="font-mono">{config.signature_header}</code> = {config.signature_scheme}. {config.idempotency}. Timestamp tolerance: {config.timestamp_tolerance}.
            </p>
            <p className="text-xs text-slate-400">{config.mode}</p>
          </div>
        </section>
      )}

      <section className="rounded-xl border-2 border-violet-500 bg-[#FAF5FF]" data-testid="simulator-panel">
        <div className="border-b border-violet-200 bg-violet-100 px-6 py-2.5">
          <span className="text-xs font-bold uppercase tracking-[0.2em] text-violet-700">Simulated environment — no live payment provider connected</span>
        </div>
        <div className="p-6">
          <h2 className="font-heading text-lg font-medium text-slate-900">Send a simulated payment event</h2>
          <p className="mt-1 text-xs text-slate-500">Events are signed with the server-side webhook secret and flow through the real ingestion pipeline, labeled SIMULATED end to end.</p>
          <div className="mt-4 grid grid-cols-2 md:grid-cols-5 gap-3">
            <input data-testid="sim-order-id" value={form.order_id} onChange={(e) => setForm({ ...form, order_id: e.target.value })}
              placeholder="order_id (blank = new)" className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none" />
            <input data-testid="sim-amount" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })}
              placeholder="amount" type="number" className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none" />
            <select data-testid="sim-currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })}
              className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none">
              {["INR", "USD", "EUR", "GBP"].map((c) => <option key={c}>{c}</option>)}
            </select>
            <select data-testid="sim-status" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}
              className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none">
              {["failed", "success", "pending"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <select data-testid="sim-failure-code" value={form.failure_code} onChange={(e) => setForm({ ...form, failure_code: e.target.value })}
              className="rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm outline-none">
              {FAILURE_CODES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button data-testid="simulate-submit-btn" onClick={sendEvent} disabled={busy}
              className="rounded-lg bg-violet-600 px-5 py-2.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-violet-700 disabled:opacity-50">
              Send simulated event
            </button>
            <button data-testid="invalid-sig-test-btn" onClick={testInvalidSignature}
              className="flex items-center gap-2 rounded-lg border border-violet-300 bg-white px-4 py-2.5 text-sm text-violet-700 transition-colors duration-200 hover:bg-violet-50">
              <ShieldAlert className="h-4 w-4" /> Test invalid signature
            </button>
          </div>

          {eventResult && (
            <div className="mt-4 rounded-lg border border-violet-200 bg-white p-4" data-testid="sim-event-result">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <StatusBadge value="SIMULATED" />
                <span className="font-mono text-xs text-slate-600">{eventResult.event_id}</span>
                <span className="text-xs text-slate-500">→ {eventResult.status} / {eventResult.result?.result}</span>
                {eventResult.result?.case_id && (
                  <button onClick={() => navigate(`/cases/${eventResult.result.case_id}`)} data-testid="sim-open-case-btn"
                    className="rounded bg-slate-900 px-2 py-1 text-xs text-white hover:bg-slate-800">
                    Open case
                  </button>
                )}
              </div>
              <p className="mt-2 text-[11px] text-slate-400">{eventResult.note}</p>
            </div>
          )}

          {sigResult && (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm" data-testid="invalid-sig-result">
              <span className="font-medium text-red-700">REJECTED — HTTP {sigResult.http_status}.</span>
              <span className="ml-2 text-red-600">Security event logged: {String(sigResult.security_event_logged)}. The forged event was never processed.</span>
            </div>
          )}
        </div>
      </section>

      <section className="rounded-xl border-2 border-violet-500 bg-[#FAF5FF]" data-testid="scenario-runner">
        <div className="border-b border-violet-200 bg-violet-100 px-6 py-2.5">
          <span className="text-xs font-bold uppercase tracking-[0.2em] text-violet-700">Demo scenario runner — all scenarios simulated</span>
        </div>
        <div className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {SCENARIOS.map((s) => (
              <button key={s.id} data-testid={`scenario-btn-${s.id}`} onClick={() => runScenario(s.id)} disabled={busy}
                className="rounded-xl border border-violet-200 bg-white p-4 text-left transition-all duration-200 hover:border-violet-400 hover:shadow-sm disabled:opacity-50">
                <div className="flex items-center gap-2">
                  <FlaskConical className="h-4 w-4 text-violet-500" />
                  <span className="text-sm font-semibold text-slate-900">{s.id}. {s.title}</span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-slate-500">{s.desc}</p>
              </button>
            ))}
          </div>

          {scenarioResult && (
            <div className="mt-6 rounded-xl border border-violet-200 bg-white p-5" data-testid="scenario-result">
              <div className="flex items-center justify-between">
                <h3 className="font-heading text-base font-medium text-slate-900">{scenarioResult.title}</h3>
                <StatusBadge value="SIMULATED" />
              </div>
              <div className="mt-4 space-y-2">
                {scenarioResult.steps.map((step, i) => (
                  <div key={i} className={`flex items-start gap-3 rounded-lg p-3 ${step.status === "highlight" ? "bg-green-50 border border-green-200" : "bg-slate-50"}`}>
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-900 text-[10px] font-bold text-white">{i + 1}</span>
                    <div>
                      <div className="text-sm font-medium text-slate-900">{step.label}</div>
                      <div className="text-xs text-slate-500">{step.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
              {scenarioResult.case_id && (
                <button onClick={() => navigate(`/cases/${scenarioResult.case_id}`)} data-testid="scenario-open-case-btn"
                  className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white transition-colors duration-200 hover:bg-slate-800">
                  Open case & decision replay
                </button>
              )}
            </div>
          )}
        </div>
      </section>

      {events && (
        <section className="rounded-xl border border-slate-200 bg-white" data-testid="webhook-events-table">
          <div className="border-b border-slate-100 p-6">
            <h2 className="font-heading text-lg font-medium text-slate-900">Recent Webhook Events</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                  <th className="px-6 py-3">Event ID</th>
                  <th className="px-6 py-3">Type</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">Result</th>
                  <th className="px-6 py-3">Mode</th>
                  <th className="px-6 py-3">Received</th>
                </tr>
              </thead>
              <tbody>
                {events.events.map((e) => (
                  <tr key={e.event_id} className="border-b border-slate-100">
                    <td className="px-6 py-3 font-mono text-xs text-slate-700">{e.event_id}</td>
                    <td className="px-6 py-3 text-xs text-slate-600">{e.type}</td>
                    <td className="px-6 py-3 text-xs text-slate-600">{e.status}</td>
                    <td className="px-6 py-3 text-xs text-slate-600">{e.result || "—"}</td>
                    <td className="px-6 py-3">{e.simulated ? <StatusBadge value="SIMULATED" /> : <StatusBadge value="WEBHOOK" />}</td>
                    <td className="px-6 py-3 font-mono text-[11px] text-slate-400">{new Date(e.received_at).toLocaleString("en-GB")}</td>
                  </tr>
                ))}
                {events.events.length === 0 && (
                  <tr><td colSpan={6} className="px-6 py-10 text-center text-sm text-slate-400">No webhook events yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {events.security_events.length > 0 && (
            <div className="border-t border-slate-100 p-6">
              <h3 className="text-xs font-bold uppercase tracking-[0.2em] text-red-600">Security events</h3>
              <ul className="mt-2 space-y-1">
                {events.security_events.map((s, i) => (
                  <li key={i} className="font-mono text-[11px] text-slate-500">
                    {s.type} · {s.path} · {s.ip || "unknown"} · {new Date(s.received_at).toLocaleString("en-GB")}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
