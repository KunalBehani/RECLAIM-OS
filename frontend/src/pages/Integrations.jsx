import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Copy, FlaskConical, RefreshCcw } from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";

const LAB_TESTS = [
  ["valid-payment-failed", "Valid payment.failed"],
  ["valid-payment-captured", "Valid payment.captured"],
  ["valid-order-paid", "Valid order.paid"],
  ["duplicate-event", "Duplicate event"],
  ["invalid-signature", "Invalid signature"],
  ["out-of-order", "Out-of-order events"],
  ["late-success", "Late success after failure"],
  ["replacement-payment", "Replacement payment"],
  ["partial-payment", "Partial payment"],
  ["unknown-event", "Unknown event type"],
  ["malformed-payload", "Malformed payload"],
  ["replayed-old-event", "Replayed old event"],
];

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");

export default function Integrations() {
  const [config, setConfig] = useState(null);
  const [health, setHealth] = useState(null);
  const [endpointPath, setEndpointPath] = useState("/api/webhooks/razorpay");
  const [liveMode, setLiveMode] = useState(null);
  const [form, setForm] = useState({ key_id: "", key_secret: "", webhook_secret: "" });
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connResult, setConnResult] = useState(null);
  const [sweepResult, setSweepResult] = useState(null);
  const [sweepBusy, setSweepBusy] = useState(false);
  const [labResult, setLabResult] = useState(null);
  const [labBusy, setLabBusy] = useState(null);

  const load = useCallback(() => {
    api.get("/integrations").then((res) => {
      setConfig(res.data.integrations[0]);
      setLiveMode(res.data.live_mode);
      setEndpointPath(res.data.webhook_endpoint_path);
    }).catch(() => {});
    api.get("/integrations/razorpay/health").then((res) => setHealth(res.data)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      await api.put("/integrations/razorpay", { ...form, mode: "TEST" });
      toast.success("Razorpay TEST MODE configuration saved");
      setConnResult(null);
      setForm({ key_id: "", key_secret: "", webhook_secret: "" });
      setShowForm(false);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setBusy(true);
    setConnResult(null);
    try {
      const res = await api.post("/integrations/razorpay/test-connection");
      setConnResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Connection test failed");
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm("Disconnect Razorpay? Stored credentials will be removed.")) return;
    await api.delete("/integrations/razorpay");
    toast.success("Integration disconnected");
    setConnResult(null);
    setSweepResult(null);
    setLabResult(null);
    load();
  };

  const copyEndpoint = async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + endpointPath);
      toast.success("Webhook endpoint copied");
    } catch {
      toast.error("Copy failed");
    }
  };

  const sweep = async () => {
    setSweepBusy(true);
    setSweepResult(null);
    try {
      const res = await api.post("/integrations/verification/sweep");
      setSweepResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Sweep failed");
    } finally {
      setSweepBusy(false);
    }
  };

  const runLab = async (name) => {
    setLabBusy(name);
    setLabResult(null);
    try {
      const res = await api.post(`/integrations/razorpay/test-lab/${name}`);
      setLabResult(res.data);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Test failed");
    } finally {
      setLabBusy(null);
    }
  };

  const status = config?.status || "NOT_CONFIGURED";
  const configured = status !== "NOT_CONFIGURED";

  return (
    <div className="space-y-10" data-testid="integrations-page">
      <div>
        <h1 className="font-heading text-3xl font-medium tracking-tight text-slate-900">Integrations</h1>
        <p className="mt-1 text-sm text-slate-500">
          Provider connections, webhook configuration, integration health and the developer test lab.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="razorpay-card">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#072654] text-white text-sm font-bold">Rz</div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="font-heading text-lg font-medium text-slate-900">Razorpay</h2>
                <StatusBadge value="TEST_MODE" />
              </div>
              <div className="text-xs text-slate-500">Payment events, orders and verification</div>
            </div>
          </div>
          <StatusBadge value={status} className="text-sm px-3 py-1" />
        </div>

        <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Key ID</div>
            <div className="mt-1 font-mono text-xs text-slate-800" data-testid="masked-key-id">{config?.key_id_masked || "—"}</div>
          </div>
          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Webhook</div>
            <div className="mt-1 text-xs text-slate-800">{config?.webhook_configured ? "Secret configured" : "Not configured"}</div>
          </div>
          <div className="rounded-lg border border-slate-100 bg-slate-50 p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">Last successful event</div>
            <div className="mt-1 font-mono text-xs text-slate-800">{fmtTime(config?.last_successful_event_at)}</div>
          </div>
        </div>

        {config?.last_error && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700" data-testid="integration-error">
            Last error ({fmtTime(config.last_error_at)}): {config.last_error}
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-3">
          {!showForm && (
            <button data-testid="configure-btn" onClick={() => setShowForm(true)}
              className="rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-slate-800">
              {configured ? "Update configuration" : "Save configuration"}
            </button>
          )}
          <button data-testid="test-connection-btn" onClick={testConnection} disabled={!configured || busy}
            className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors duration-200 hover:bg-slate-50 disabled:opacity-40">
            Test connection
          </button>
          <button data-testid="disconnect-btn" onClick={disconnect} disabled={!configured}
            className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm font-medium text-red-700 transition-colors duration-200 hover:bg-red-100 disabled:opacity-40">
            Disconnect
          </button>
        </div>

        {connResult && (
          <div className={`mt-4 flex items-center gap-2 rounded-lg border p-3 text-sm ${connResult.status === "CONNECTED" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-700"}`} data-testid="test-connection-result">
            {connResult.status === "CONNECTED" ? <CheckCircle2 className="h-4 w-4" /> : <XCircleIcon />}
            {connResult.status === "CONNECTED"
              ? `CONNECTED — provider API authenticated successfully (mode: ${connResult.detail?.mode}).`
              : `ERROR — ${connResult.detail}`}
          </div>
        )}

        {showForm && (
          <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-5" data-testid="config-form">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Key ID (rzp_test_…)</label>
                <input data-testid="key-id-input" type="text" value={form.key_id} onChange={(e) => setForm({ ...form, key_id: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" placeholder="rzp_test_…" autoComplete="off" />
              </div>
              <div>
                <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Key secret</label>
                <input data-testid="key-secret-input" type="password" value={form.key_secret} onChange={(e) => setForm({ ...form, key_secret: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" autoComplete="off" />
              </div>
              <div>
                <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Webhook secret</label>
                <input data-testid="webhook-secret-input" type="password" value={form.webhook_secret} onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" autoComplete="off" />
              </div>
            </div>
            <p className="mt-3 text-xs text-slate-400">
              Secrets are stored server-side only and never returned by any API. Only TEST mode keys (rzp_test_…) are accepted in this phase.
            </p>
            <div className="mt-4 flex gap-3">
              <button data-testid="save-integration-btn" onClick={save} disabled={busy}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50">
                Save configuration
              </button>
              <button onClick={() => setShowForm(false)} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="webhook-endpoint-card">
        <h2 className="font-heading text-lg font-medium text-slate-900">Webhook Endpoint</h2>
        <p className="mt-1 text-xs text-slate-500">
          In the Razorpay Dashboard (Test Mode) → Settings → Webhooks, point a new webhook at this URL with your webhook secret.
          Enable events: payment.authorized, payment.captured, payment.failed, order.paid.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <code className="max-w-full overflow-x-auto break-all rounded-lg bg-slate-100 px-3 py-2 font-mono text-xs text-slate-800" data-testid="webhook-endpoint-url">
            {typeof window !== "undefined" ? window.location.origin : ""}{endpointPath}
          </code>
          <button data-testid="copy-endpoint-btn" onClick={copyEndpoint}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">
            <Copy className="h-3.5 w-3.5" /> Copy endpoint
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="integration-health">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-heading text-lg font-medium text-slate-900">Integration Health</h2>
          <button data-testid="sweep-btn" onClick={sweep} disabled={sweepBusy}
            className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            <RefreshCcw className={`h-4 w-4 ${sweepBusy ? "animate-spin" : ""}`} /> Run verification sweep
          </button>
        </div>
        {health ? (
          <div className="mt-4 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200">
            {[
              ["Events received", health.events_received],
              ["Processed", health.events_processed],
              ["Duplicates ignored", health.duplicates_ignored],
              ["Signature failures", health.signature_failures],
              ["Provider cases", health.cases_created_from_provider],
              ["Recovered outcomes", health.recovered_outcomes_detected],
            ].map(([label, value]) => (
              <div key={label} className="bg-white p-4">
                <div className="text-xl font-semibold tabular-nums text-slate-900" data-testid={`health-${label.toLowerCase().replace(/ /g, "-")}`}>{value}</div>
                <div className="mt-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-4 h-24 animate-pulse rounded-lg bg-slate-100" />
        )}
        {health && (
          <div className="mt-3 text-xs text-slate-500 font-mono">
            Last webhook: {fmtTime(health.last_webhook_at)} {health.last_webhook_type ? `(${health.last_webhook_type})` : ""} ·
            Last failed: {fmtTime(health.last_failed_event_at)}
          </div>
        )}
        {sweepResult && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs font-mono text-slate-700" data-testid="sweep-result">
            {Object.entries(sweepResult).map(([k, v]) => <span key={k} className="mr-4">{k}: {v}</span>)}
          </div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="webhook-test-lab">
        <div className="flex items-center gap-2">
          <FlaskConical className="h-5 w-5 text-slate-400" />
          <h2 className="font-heading text-lg font-medium text-slate-900">Webhook Test Lab</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Each test delivers genuinely signed Razorpay-format payloads through the real webhook endpoint — the same code path as provider traffic.
          Requires a configured webhook secret.
        </p>
        <div className="mt-4 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {LAB_TESTS.map(([name, label]) => (
            <button key={name} data-testid={`test-lab-btn-${name}`} onClick={() => runLab(name)} disabled={!configured || labBusy !== null}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-left text-xs font-medium text-slate-700 transition-colors duration-200 hover:border-slate-400 hover:bg-slate-50 disabled:opacity-40">
              {label}
            </button>
          ))}
        </div>
        {!configured && <p className="mt-3 text-xs text-amber-600">Save a Razorpay TEST MODE configuration first.</p>}
        {labResult && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4" data-testid="test-lab-result">
            <div className="text-sm font-medium text-slate-900">{labResult.test} <span className="ml-2 font-mono text-[10px] text-slate-400">{labResult.order_id}</span></div>
            <div className="mt-2 space-y-1.5">
              {labResult.steps.map((s, i) => (
                <div key={i} className="flex items-center gap-3 text-xs">
                  <span className={`font-mono font-semibold ${s.http < 300 ? "text-green-600" : "text-red-600"}`}>HTTP {s.http}</span>
                  <span className="text-slate-700">{s.label}</span>
                  <span className="font-mono text-slate-400">{s.result}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6" data-testid="live-mode-card">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-heading text-lg font-medium text-slate-500">Razorpay — LIVE MODE</h2>
            <p className="mt-1 text-xs text-slate-400">{liveMode?.note || "Not available."}</p>
          </div>
          <StatusBadge value="NOT_CONFIGURED" />
        </div>
      </section>
    </div>
  );
}

function XCircleIcon() {
  return <span className="inline-block h-4 w-4 rounded-full bg-red-600 text-center text-[10px] font-bold leading-4 text-white">!</span>;
}
