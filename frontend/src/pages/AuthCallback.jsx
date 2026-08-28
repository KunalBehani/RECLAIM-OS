import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import api from "../api";
import { useAuth } from "../context/AuthContext";

export default function AuthCallback() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setUser } = useAuth();
  const hasProcessed = useRef(false);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;
    const params = new URLSearchParams(location.hash.replace(/^#/, ""));
    const sessionId = params.get("session_id");
    if (!sessionId) {
      navigate("/login", { replace: true });
      return;
    }
    api
      .post("/auth/session", { session_id: sessionId })
      .then((res) => {
        window.history.replaceState(null, "", window.location.pathname);
        setUser(res.data);
        navigate("/", { state: { user: res.data }, replace: true });
      })
      .catch(() => navigate("/login", { replace: true }));
  }, [location, navigate, setUser]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC]">
      <div data-testid="auth-callback-loading" className="text-center">
        <div className="mx-auto h-10 w-10 animate-pulse rounded-lg bg-slate-900" />
        <p className="mt-4 text-sm text-slate-500">Establishing secure session…</p>
      </div>
    </div>
  );
}
