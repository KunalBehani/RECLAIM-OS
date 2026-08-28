import { Navigate } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const LOGIN_BG =
  "https://images.unsplash.com/photo-1517816743773-6e0fd518b4a6?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzMjh8MHwxfHNlYXJjaHwyfHxtaW5pbWFsJTIwYWJzdHJhY3QlMjBhcmNoaXRlY3R1cmFsJTIwYXJjaGl0ZWN0dXJlJTIwd2hpdGV8ZW58MHx8fHwxNzg3OTAxODM4fDA&ixlib=rb-4.1.0&q=85";

export default function Login() {
  const { user, loading } = useAuth();

  if (!loading && user) return <Navigate to="/" replace />;

  const startGoogleLogin = () => {
    // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
    const redirectUrl = window.location.origin + "/";
    window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
  };

  return (
    <div className="flex min-h-screen bg-white">
      <div
        className="hidden lg:block lg:w-1/2 bg-cover bg-center relative"
        style={{ backgroundImage: `url(${LOGIN_BG})` }}
      >
        <div className="absolute inset-0 bg-slate-900/70" />
        <div className="absolute bottom-0 left-0 p-12">
          <div className="flex items-center gap-3 mb-6">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white">
              <ShieldCheck className="h-6 w-6 text-slate-900" />
            </div>
            <span className="font-heading text-2xl font-semibold text-white">RECLAIM OS</span>
          </div>
          <h1 className="font-heading text-4xl font-light tracking-tight text-white max-w-md leading-tight">
            Revenue at risk doesn't have to become revenue lost.
          </h1>
          <p className="mt-4 max-w-md text-sm text-slate-300 leading-relaxed">
            Detect genuinely unresolved revenue, evaluate the highest-value recovery options,
            execute only policy-permitted actions, and verify every outcome before claiming recovery.
          </p>
        </div>
      </div>
      <div className="flex w-full lg:w-1/2 items-center justify-center px-6">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900">
              <ShieldCheck className="h-5 w-5 text-white" />
            </div>
            <span className="font-heading text-xl font-semibold text-slate-900">RECLAIM OS</span>
          </div>
          <h2 className="font-heading text-2xl font-medium tracking-tight text-slate-900">Sign in</h2>
          <p className="mt-2 text-sm text-slate-500">
            Access the recovery control plane with your authorized Google account.
          </p>
          <button
            data-testid="google-login-btn"
            onClick={startGoogleLogin}
            className="mt-8 flex w-full items-center justify-center gap-3 rounded-lg bg-slate-900 px-4 py-3 text-sm font-medium text-white transition-colors duration-200 hover:bg-slate-800"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24">
              <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" />
              <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" opacity=".7" />
              <path fill="currentColor" d="M5.84 14.1a7.2 7.2 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84z" opacity=".5" />
              <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" opacity=".9" />
            </svg>
            Continue with Google
          </button>
          <p className="mt-6 text-xs text-slate-400 leading-relaxed">
            Policy-bounded autonomy: AI recommends, deterministic policy decides, humans approve,
            and only independently verified outcomes count as recovered revenue.
          </p>
        </div>
      </div>
    </div>
  );
}
