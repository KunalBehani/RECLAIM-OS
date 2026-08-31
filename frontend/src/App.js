import "@/App.css";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import AuthCallback from "@/pages/AuthCallback";
import Dashboard from "@/pages/Dashboard";
import CaseDetail from "@/pages/CaseDetail";
import Ingest from "@/pages/Ingest";
import ReviewQueue from "@/pages/ReviewQueue";
import Events from "@/pages/Events";
import Integrations from "@/pages/Integrations";
import PayRetry from "@/pages/PayRetry";

function Protected({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (location.state?.user) return children;
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#F8FAFC]" data-testid="auth-loading">
        <div className="h-10 w-10 animate-pulse rounded-lg bg-slate-900" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRouter() {
  const location = useLocation();
  // Detect the OAuth session_id synchronously during render (not in useEffect)
  // to avoid race conditions with the auth check.
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/pay/:token" element={<PayRetry />} />
      <Route path="/" element={<Protected><Layout /></Protected>}>
        <Route index element={<Dashboard />} />
        <Route path="cases/:caseId" element={<CaseDetail />} />
        <Route path="ingest" element={<Ingest />} />
        <Route path="review" element={<ReviewQueue />} />
        <Route path="events" element={<Events />} />
        <Route path="integrations" element={<Integrations />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </AuthProvider>
  );
}

export default App;
