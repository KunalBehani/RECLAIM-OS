import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";

const API = process.env.REACT_APP_BACKEND_URL;

const loadCheckoutJs = () => new Promise((resolve, reject) => {
  if (window.Razorpay) return resolve();
  const s = document.createElement("script");
  s.src = "https://checkout.razorpay.com/v1/checkout.js";
  s.onload = () => resolve();
  s.onerror = () => reject(new Error("Could not load the secure payment window."));
  document.body.appendChild(s);
});

export default function PayRetry() {
  const { token } = useParams();
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    axios.get(`${API}/api/recovery/pay/${token}`)
      .then((r) => setState({ status: "ready", ...r.data }))
      .catch((e) => setState({ status: "error", detail: e.response?.data?.detail || "This recovery link is invalid or expired." }));
  }, [token]);

  const launch = async () => {
    setState((s) => ({ ...s, status: "opening" }));
    try {
      await loadCheckoutJs();
      const rzp = new window.Razorpay({
        key: state.key_id,
        order_id: state.order_id,
        amount: state.amount_paise,
        currency: state.currency,
        name: state.merchant,
        description: `Retry payment for order ${state.order_id}`,
        theme: { color: "#072654" },
        handler: async (resp) => {
          try {
            await axios.post(`${API}/api/recovery/pay/${token}/complete`, resp);
            setState((s) => ({ ...s, status: "success", payment_id: resp.razorpay_payment_id }));
          } catch {
            setState((s) => ({ ...s, status: "verify_error" }));
          }
        },
        modal: { ondismiss: () => setState((s) => ({ ...s, status: "ready" })) },
      });
      rzp.on("payment.failed", () => setState((s) => ({ ...s, status: "ready", note: "That attempt did not go through — you can try again." })));
      rzp.open();
    } catch (e) {
      setState((s) => ({ ...s, status: "error", detail: e.message }));
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC] p-6" data-testid="pay-retry-page">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <p className="text-xs font-bold uppercase tracking-widest text-[#072654]">RECLAIM OS</p>
        <h1 className="mt-2 text-xl font-semibold text-slate-900">Complete your payment</h1>

        {state.status === "loading" && <p className="mt-4 text-sm text-slate-500" data-testid="pay-retry-loading">Loading secure checkout…</p>}
        {state.status === "error" && <p className="mt-4 text-sm text-red-600" data-testid="pay-retry-error">{state.detail}</p>}
        {state.status === "success" && (
          <div className="mt-4" data-testid="pay-retry-success">
            <p className="text-sm text-emerald-700 font-medium">Payment received.</p>
            <p className="mt-1 text-xs text-slate-500">Reference <span className="font-mono">{state.payment_id}</span>. Your merchant has been notified automatically. You can close this page.</p>
          </div>
        )}
        {state.status === "verify_error" && (
          <p className="mt-4 text-sm text-amber-700" data-testid="pay-retry-verify-error">Your payment was processed but confirmation could not be recorded — your merchant will still be notified by the payment provider.</p>
        )}
        {(state.status === "ready" || state.status === "opening") && (
          <div className="mt-4">
            {state.settled ? (
              <p className="text-sm text-emerald-700" data-testid="pay-retry-settled">This order has already been paid. No further action is needed.</p>
            ) : (
              <>
                <p className="text-sm text-slate-600">Order <span className="font-mono text-xs">{state.order_id}</span></p>
                <p className="mt-1 text-2xl font-bold text-slate-900" data-testid="pay-retry-amount">₹{((state.amount_paise || 0) / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</p>
                {state.note && <p className="mt-2 text-xs text-amber-700">{state.note}</p>}
                <button data-testid="pay-retry-launch-btn" onClick={launch} disabled={state.status === "opening"}
                  className="mt-5 w-full rounded-lg bg-[#072654] px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-[#0a3168] disabled:opacity-40">
                  {state.status === "opening" ? "Opening secure checkout…" : "Pay securely via Razorpay"}
                </button>
                <p className="mt-3 text-[11px] text-slate-500">Secured by Razorpay. We never ask for your password or card details outside the secure payment window.</p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
