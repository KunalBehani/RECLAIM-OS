import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import {
  ShieldCheck,
  LayoutDashboard,
  UploadCloud,
  ClipboardCheck,
  Radio,
  LogOut,
  OctagonAlert,
  Menu,
  X,
  Plug,
  FlaskConical,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import api from "../api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, testId: "nav-dashboard", end: true },
  { to: "/ingest", label: "Ingest Data", icon: UploadCloud, testId: "nav-ingest" },
  { to: "/review", label: "Review Queue", icon: ClipboardCheck, testId: "nav-review" },
  { to: "/evaluation", label: "Evaluation Lab", icon: FlaskConical, testId: "nav-evaluation" },
  { to: "/events", label: "Events & Simulator", icon: Radio, testId: "nav-events" },
  { to: "/integrations", label: "Integrations", icon: Plug, testId: "nav-integrations" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [settings, setSettings] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    api.get("/settings").then((res) => setSettings(res.data.settings)).catch(() => {});
  }, []);

  const toggleEmergencyStop = async () => {
    const next = !settings?.emergency_stop;
    const res = await api.put("/settings", { emergency_stop: next });
    setSettings(res.data.settings);
  };

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="min-h-screen bg-[#F8FAFC]">
      <div className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-3 border-b border-slate-200 bg-white px-4 lg:hidden">
        <button
          data-testid="mobile-menu-btn"
          onClick={() => setSidebarOpen(true)}
          className="rounded-md p-2 text-slate-600 transition-colors duration-200 hover:bg-slate-100"
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-900">
            <ShieldCheck className="h-4 w-4 text-white" />
          </div>
          <span className="font-heading text-base font-semibold tracking-tight text-slate-900">RECLAIM OS</span>
        </div>
      </div>

      {sidebarOpen && (
        <div
          data-testid="sidebar-overlay"
          className="fixed inset-0 z-30 bg-slate-900/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 w-64 border-r border-slate-200 bg-white flex flex-col transition-transform duration-300 ease-out lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center gap-3 px-6 py-6 border-b border-slate-100">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900">
            <ShieldCheck className="h-5 w-5 text-white" />
          </div>
          <div className="flex-1">
            <div className="font-heading text-lg font-semibold tracking-tight text-slate-900">RECLAIM OS</div>
            <div className="text-[11px] text-slate-500">AI Revenue Recovery, with Control</div>
          </div>
          <button
            data-testid="mobile-menu-close-btn"
            onClick={() => setSidebarOpen(false)}
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              data-testid={item.testId}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors duration-200 ${
                  isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`
              }
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-100 px-4 py-4 space-y-3">
          <button
            data-testid="emergency-stop-toggle"
            onClick={toggleEmergencyStop}
            className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors duration-200 ${
              settings?.emergency_stop
                ? "border-red-300 bg-red-50 text-red-700"
                : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
            }`}
          >
            <OctagonAlert className="h-4 w-4" />
            {settings?.emergency_stop ? "Emergency stop: ON" : "Emergency stop: off"}
          </button>
          <div className="flex items-center gap-3">
            {user?.picture ? (
              <img src={user.picture} alt="" className="h-8 w-8 rounded-full border border-slate-200" referrerPolicy="no-referrer" />
            ) : (
              <div className="h-8 w-8 rounded-full bg-slate-200" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-800">{user?.name || "User"}</div>
              <div className="truncate text-xs text-slate-500">{user?.role || ""}</div>
            </div>
            <button
              data-testid="logout-btn"
              onClick={handleLogout}
              className="rounded-md p-1.5 text-slate-400 transition-colors duration-200 hover:bg-slate-100 hover:text-slate-700"
              title="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="pt-14 lg:pt-0 lg:pl-64">
        {settings?.emergency_stop && (
          <div data-testid="emergency-stop-banner" className="bg-red-600 px-6 py-2 text-center text-sm font-medium text-white">
            EMERGENCY STOP ENABLED — all autonomous recovery actions are halted by policy
          </div>
        )}
        <main className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
