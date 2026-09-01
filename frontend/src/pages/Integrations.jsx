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
  const [resend, setResend] = useState(null);
  const [resendBusy, setResendBusy] = useState(false);

  const loadResend = useCallback(() => {
    api.get("/integrations/resend").then((res) => setResend(res.data)).catch(() => {});
  }, []);

  const toggleResend = async (enabled) => {
    setResendBusy(true);
    try {
      await api.put("/integrations/resend/config", { enabled });
      toast.success(enabled ? "Customer notifications ENABLED (real emails will be sent)" : "Customer notifications disabled");
      loadResend();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Update failed");
    } finally {
      setResendBusy(false);
    }
  };

  const testResend = async () => {
    setResendBusy(true);
    try {
      const res = await api.post("/integrations/resend/test-connection");
      if (res.data.status === "CONNECTED") toast.success(res.data.detail);
      else toast.error(res.data.detail || "Notification test failed");
      loadResend();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Notification test failed");
    } finally {
      setResendBusy(false);
    }
  };

  const [live, setLive] = useState(null);
  const [liveForm, setLiveForm] = useState({ key_id: "", key_secret: "", webhook_secret: "" });
  const [liveConfirm, setLiveConfirm] = useState("");
  const [liveBusy, setLiveBusy] = useState(false);

  const loadLive = useCallback(() => {
    api.get("/integrations/razorpay/live").then((res) => setLive(res.data)).catch(() => {});
  }, []);

  const liveCall = async (fn, okMsg) => {
    setLiveBusy(true);
    try {
      const res = await fn();
      if (res.data?.status === "ERROR") toast.error(res.data.detail || "LIVE check failed");
      else toast.success(okMsg);
      loadLive();
    } catch (e) {
      toast.error(e.response?.data?.detail || "LIVE operation failed");
    } finally {
      setLiveBusy(false);
    }
  };

  const saveLiveConfig = async () => {
    setLiveBusy(true);
    try {
      await api.put("/integrations/razorpay/live/config", liveForm);
      // Write-only contract extends to the client: never retain secrets in DOM/state after save.
      setLiveForm({ key_id: "", key_secret: "", webhook_secret: "" });
      setLiveConfirm("");
      toast.success("LIVE credentials saved (write-only). Activation reset — re-confirm to activate.");
      loadLive();
    } catch (e) {
      toast.error(e.response?.data?.detail || "LIVE operation failed");
    } finally {
      setLiveBusy(false);
    }
  };
  const testLiveConnection = () => liveCall(() => api.post("/integrations/razorpay/live/test-connection"), "LIVE connection CONNECTED (genuine provider response)");
  const activateLive = () => liveCall(() => api.post("/integrations/razorpay/live/activate", { confirmation: liveConfirm }), "LIVE mode ACTIVATED — live webhooks are now accepted");
  const deactivateLive = () => liveCall(() => api.post("/integrations/razorpay/live/deactivate"), "LIVE mode deactivated");

  const load = useCallback(() => {
    api.get("/integrations").then((res) => {
      setConfig(res.data.integrations[0]);
      setLiveMode(res.data.live_mode);
      setEndpointPath(res.data.webhook_endpoint_path);
    }).catch(() => {});
    api.get("/integrations/razorpay/health").then((res) => setHealth(res.data)).catch(() => {});
    loadResend();
    loadLive();
  }, [loadResend, loadLive]);
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

  const [checkoutAmount, setCheckoutAmount] = useState("500");
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [checkoutState, setCheckoutState] = useState(null);

  const loadCheckoutJs = () => new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Failed to load Razorpay Checkout script"));
    document.body.appendChild(s);
  });

  const createTestPayment = async () => {
    setCheckoutBusy(true);
    setCheckoutState(null);
    try {
      const res = await api.post("/integrations/razorpay/test-checkout/order", { amount: Number(checkoutAmount) });
      const d = res.data;
      if (d.status !== "READY") {
        setCheckoutState(d);
        toast.error(d.detail || "Genuine order creation failed at provider");
        return;
      }
      await loadCheckoutJs();
      const rzp = new window.Razorpay({
        key: d.key_id,
        order_id: d.order_id,
        amount: d.amount_paise,
        currency: d.currency,
        name: "RECLAIM OS — Phase 1 Verification",
        description: `Genuine TEST order ${d.order_id}`,
        theme: { color: "#072654" },
        handler: () => { toast.success("Payment authorized — awaiting genuine provider webhooks."); load(); },
        modal: { ondismiss: () => toast.info("Checkout closed.") },
      });
      rzp.on("payment.failed", () => {
        toast.info("payment.failed emitted — the genuine webhook arrives via the registered endpoint shortly.");
        load();
      });
      rzp.open();
      setCheckoutState(d);
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message || "Checkout launch failed");
    } finally {
      setCheckoutBusy(false);
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

      <section className="rounded-xl border-2 border-amber-400 bg-amber-50/40 p-6" data-testid="live-section">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-heading text-lg font-medium text-slate-900">Razorpay LIVE — Production Mode</h2>
            <p className="mt-1 text-xs text-slate-600">
              Phase 2A readiness: completely isolated credentials, webhook secret and endpoint
              (<span className="font-mono">/api/webhooks/razorpay/live</span>). Ingestion, verification,
              reconciliation and audit only — <strong>no real-money recovery execution</strong>.
            </p>
          </div>
          <StatusBadge value={live?.activated ? "ACTIVE" : live?.configured ? (live?.live?.status || "NOT_CONNECTED") : "NOT_CONFIGURED"} />
        </div>

        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-100/70 p-3 text-xs font-medium text-amber-900" data-testid="live-warning">
          PRODUCTION WARNING — LIVE mode processes real-money payment events. LIVE actions are disabled by default
          {live ? ` (currently: ${live.live_actions_enabled ? "ENABLED" : "disabled"})` : ""}.
          Credentials are write-only and never readable back. Activation requires typing the confirmation phrase.
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <input data-testid="live-key-id-input" type="text" placeholder="rzp_live_…" value={liveForm.key_id}
            onChange={(e) => setLiveForm({ ...liveForm, key_id: e.target.value })}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" />
          <input data-testid="live-key-secret-input" type="password" placeholder="Key Secret (write-only)" value={liveForm.key_secret}
            onChange={(e) => setLiveForm({ ...liveForm, key_secret: e.target.value })}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" />
          <input data-testid="live-webhook-secret-input" type="password" placeholder="Live Webhook Secret (write-only)" value={liveForm.webhook_secret}
            onChange={(e) => setLiveForm({ ...liveForm, webhook_secret: e.target.value })}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button data-testid="live-save-btn" onClick={saveLiveConfig} disabled={liveBusy}
            className="rounded-lg bg-[#072654] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#0a3168] disabled:opacity-40">
            Save LIVE credentials
          </button>
          <button data-testid="live-test-btn" onClick={testLiveConnection} disabled={liveBusy || !live?.configured}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-40">
            Test LIVE connection (read-only)
          </button>
          {live?.configured && !live?.activated && (
            <div className="flex items-center gap-2" data-testid="live-activation-box">
              <input data-testid="live-confirm-input" type="text" placeholder='Type "ACTIVATE LIVE"' value={liveConfirm}
                onChange={(e) => setLiveConfirm(e.target.value)}
                className="w-44 rounded-lg border border-amber-400 bg-white px-3 py-2 font-mono text-xs outline-none" />
              <button data-testid="live-activate-btn" onClick={activateLive} disabled={liveBusy || liveConfirm !== "ACTIVATE LIVE"}
                className="rounded-lg bg-amber-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-amber-700 disabled:opacity-40">
                Activate LIVE
              </button>
            </div>
          )}
          {live?.activated && (
            <button data-testid="live-deactivate-btn" onClick={deactivateLive} disabled={liveBusy}
              className="rounded-lg border border-red-300 bg-white px-4 py-2.5 text-sm font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-40">
              Deactivate LIVE
            </button>
          )}
        </div>

        {live?.activated && (
          <p className="mt-3 text-xs text-emerald-700" data-testid="live-active-note">
            LIVE mode active (by {live.activated_by}). Live webhooks are accepted at <span className="font-mono">{live.webhook_endpoint_path}</span>.
            Register that URL in your Razorpay dashboard (Live Mode) with the live webhook secret.
          </p>
        )}
        {live?.live?.last_error && (
          <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700" data-testid="live-error">{live.live.last_error}</div>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="resend-section">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-heading text-lg font-medium text-slate-900">Customer Notifications — Resend (Email)</h2>
            <p className="mt-1 text-xs text-slate-500">
              Genuine customer-facing recovery emails for provider cases. When enabled, an eligible Razorpay TEST case
              triggers a REAL recovery email with a same-order secure retry link — the only execution mode that can earn
              recovery attribution. When disabled, execution stays SIMULATED and never earns attribution.
              The API key lives server-side only and is never exposed to this page.
            </p>
          </div>
          <StatusBadge value={resend?.status || "NOT_CONFIGURED"} />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {!resend?.enabled ? (
            <button data-testid="resend-enable-btn" onClick={() => toggleResend(true)} disabled={resendBusy}
              className="rounded-lg bg-[#072654] px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-[#0a3168] disabled:opacity-40">
              Enable notifications
            </button>
          ) : (
            <button data-testid="resend-disable-btn" onClick={() => toggleResend(false)} disabled={resendBusy}
              className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-40">
              Disable
            </button>
          )}
          <button data-testid="resend-test-btn" onClick={testResend} disabled={resendBusy}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-40">
            {resendBusy ? "Working…" : "Send test email (genuine)"}
          </button>
          {resend?.last_test_at && <span className="text-xs text-slate-500" data-testid="resend-last-test">Last test: {fmtTime(resend.last_test_at)}</span>}
        </div>
        {resend?.last_error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700" data-testid="resend-error">{resend.last_error}</div>}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="test-checkout-section">
        <h2 className="font-heading text-lg font-medium text-slate-900">Real Test Checkout — Phase 1 Verification</h2>
        <p className="mt-1 text-xs text-slate-500">
          Creates a genuine Razorpay TEST order via the provider API (nothing simulated), then opens Razorpay Standard Checkout.
          To fail the payment intentionally, use <span className="font-mono">failure@razorpay</span> as the email in Checkout.
          The genuine payment.failed webhook then flows through signature verification, detection and case creation —
          watch Integration Health and Events. Requires the webhook endpoint registered in your Razorpay dashboard.
        </p>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-slate-500">Amount (INR)</label>
            <input data-testid="test-checkout-amount-input" type="number" min="1" max="100000" value={checkoutAmount}
              onChange={(e) => setCheckoutAmount(e.target.value)}
              className="mt-1 w-36 rounded-lg border border-slate-200 bg-white px-3 py-2 font-mono text-sm outline-none" />
          </div>
          <button data-testid="create-test-payment-btn" onClick={createTestPayment} disabled={!configured || checkoutBusy}
            className="rounded-lg bg-[#072654] px-4 py-2.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-[#0a3168] disabled:opacity-40">
            {checkoutBusy ? "Creating order…" : "Create Test Payment"}
          </button>
        </div>
        {checkoutState?.status === "READY" && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs" data-testid="test-checkout-result">
            <span className="font-medium text-slate-900">Checkout launched for genuine TEST order</span>{" "}
            <span className="font-mono text-slate-700">{checkoutState.order_id}</span>{" "}
            <span className="text-slate-500">— ₹{checkoutState.amount_inr} INR. Complete or fail the payment in the Razorpay window.</span>
          </div>
        )}
        {checkoutState?.status === "ERROR" && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700" data-testid="test-checkout-error">
            ERROR — {checkoutState.detail}
          </div>
        )}
        {!configured && <p className="mt-3 text-xs text-amber-600">Connect Razorpay TEST MODE first.</p>}
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
