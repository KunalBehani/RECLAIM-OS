import { useCallback, useEffect, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis, ReferenceLine,
} from "recharts";
import {
  AlertTriangle, CheckCircle2, ChevronRight, Download, FlaskConical,
  HelpCircle, Info, RefreshCw, Sliders, Trash2, X, Plus,
} from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import StatusBadge from "../components/StatusBadge";
import { Money, formatMoney } from "../components/Money";

function TooltipHelp({ text }) {
  return (
    <span className="group relative ml-1 inline-flex cursor-help items-center text-slate-400 hover:text-slate-600">
      <HelpCircle className="h-3.5 w-3.5" />
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 hidden w-56 -translate-x-1/2 rounded bg-slate-900 px-2.5 py-1.5 text-center text-[11px] font-normal text-white opacity-0 shadow-lg transition-opacity group-hover:block group-hover:opacity-100">
        {text}
      </span>
    </span>
  );
}

function StatCard({ label, value, sub, tooltip, accent = "border-l-blue-500", testId }) {
  return (
    <div data-testid={testId} className={`rounded-xl border border-slate-200 border-l-4 ${accent} bg-white p-5 shadow-sm`}>
      <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-slate-500">
        <span>{label}</span>
        {tooltip && <TooltipHelp text={tooltip} />}
      </div>
      <div className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500 leading-snug">{sub}</div>}
    </div>
  );
}

export default function EvaluationLab() {
  const [cohortsData, setCohortsData] = useState(null);
  const [selectedCohort, setSelectedCohort] = useState("GENUINE_TEST");
  const [selectedModel, setSelectedModel] = useState("");
  const [threshold, setThreshold] = useState(0.50);
  const [report, setReport] = useState(null);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("performance");
  const [runModalOpen, setRunModalOpen] = useState(false);
  const [runTitle, setRunTitle] = useState("");
  const [runNotes, setRunNotes] = useState("");
  const [savingRun, setSavingRun] = useState(false);

  // Model comparison state
  const [compModelA, setCompModelA] = useState("claude-sonnet-4-6");
  const [compModelB, setCompModelB] = useState("heuristic-fallback-v1");
  const [compResult, setCompResult] = useState(null);
  const [comparing, setComparing] = useState(false);

  const loadCohorts = useCallback(async () => {
    try {
      const res = await api.get("/evaluation/cohorts");
      setCohortsData(res.data);
    } catch {
      // transient
    }
  }, []);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const params = { cohort: selectedCohort, threshold };
      if (selectedModel) params.model_version = selectedModel;
      const res = await api.get("/evaluation/summary", { params });
      setReport(res.data);
    } catch {
      toast.error("Failed to load evaluation report.");
    } finally {
      setLoading(false);
    }
  }, [selectedCohort, threshold, selectedModel]);

  const loadRuns = useCallback(async () => {
    try {
      const res = await api.get("/evaluation/runs");
      setRuns(res.data.runs || []);
    } catch {
      // transient
    }
  }, []);

  useEffect(() => {
    loadCohorts();
    loadRuns();
  }, [loadCohorts, loadRuns]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const handleCreateRun = async (e) => {
    e.preventDefault();
    setSavingRun(true);
    try {
      const payload = {
        cohort: selectedCohort,
        threshold,
        model_version: selectedModel || null,
        title: runTitle || undefined,
        notes: runNotes || undefined,
      };
      await api.post("/evaluation/runs", payload);
      toast.success("Immutable evaluation run snapshot saved.");
      setRunModalOpen(false);
      setRunTitle("");
      setRunNotes("");
      loadRuns();
    } catch {
      toast.error("Failed to save evaluation run.");
    } finally {
      setSavingRun(false);
    }
  };

  const handleDeleteRun = async (runId) => {
    if (!window.confirm("Delete this frozen evaluation snapshot?")) return;
    try {
      await api.delete(`/evaluation/runs/${runId}`);
      toast.success("Evaluation snapshot deleted.");
      loadRuns();
    } catch {
      toast.error("Failed to delete run.");
    }
  };

  const handleCompare = async () => {
    setComparing(true);
    try {
      const res = await api.post("/evaluation/compare", {
        cohort: selectedCohort,
        model_a: compModelA,
        model_b: compModelB,
        threshold,
      });
      setCompResult(res.data);
    } catch {
      toast.error("Model comparison failed.");
    } finally {
      setComparing(false);
    }
  };

  const fmtPercent = (val) => (val != null ? `${Math.round(val * 100)}%` : "—");
  const fmtFloat = (val, digits = 4) => (val != null ? val.toFixed(digits) : "—");

  return (
    <div className="space-y-8 pb-12" data-testid="evaluation-lab">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-6">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-blue-600">
            <FlaskConical className="h-4 w-4" /> Machine Learning Evaluation & Calibration
          </div>
          <h1 className="mt-1 font-heading text-3xl font-medium tracking-tight text-slate-900">
            Model Evaluation Lab
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Empirical measurement of AI recovery estimates against authoritative payment outcomes.
            Synthetic data is strictly isolated; small datasets report sample limitations honestly.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setRunModalOpen(true)}
            data-testid="freeze-run-btn"
            className="flex items-center gap-1.5 rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-medium text-white shadow hover:bg-slate-800 transition-colors"
          >
            <Plus className="h-4 w-4" /> Freeze Evaluation Snapshot
          </button>
        </div>
      </div>

      {/* Cohort & Model Selector Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="cohort-selector-bar">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-slate-500">Cohort:</span>
          {cohortsData?.cohorts?.map((c) => (
            <button
              key={c.id}
              data-testid={`cohort-btn-${c.id.toLowerCase()}`}
              onClick={() => setSelectedCohort(c.id)}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                selectedCohort === c.id
                  ? "bg-blue-600 text-white shadow-sm"
                  : "bg-slate-50 text-slate-700 hover:bg-slate-100 border border-slate-200"
              }`}
            >
              <span>{c.label}</span>
              <span className={`rounded px-1.5 py-0.2 text-[10px] font-bold ${selectedCohort === c.id ? "bg-blue-700 text-blue-100" : "bg-slate-200 text-slate-600"}`}>
                {c.count}
              </span>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <label className="text-xs font-semibold text-slate-500">Model:</label>
          <select
            data-testid="model-version-select"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none"
          >
            <option value="">All Models (Blended)</option>
            {cohortsData?.available_model_versions?.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>

      {/* LAB Warning Banner if LAB cohort is selected */}
      {selectedCohort === "LAB" && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-900" data-testid="lab-warning-banner">
          <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600 mt-0.5" />
          <div className="text-xs leading-relaxed">
            <span className="font-bold">LAB DATA — NOT REAL-WORLD PERFORMANCE:</span> This dataset consists of synthetic webhook deliveries and Developer Test Lab scenarios (order_LAB*). Metrics here reflect developer tests, not merchant payment behavior.
          </div>
        </div>
      )}

      {/* Sample-Size Status Banner */}
      {report && report.sample_size && (
        <div
          data-testid="sample-size-banner"
          className={`flex items-start gap-3 rounded-xl border p-4 text-xs ${
            report.sample_size.status === "INSUFFICIENT_DATA"
              ? "border-red-200 bg-red-50 text-red-900"
              : report.sample_size.status === "DESCRIPTIVE_ONLY"
                ? "border-amber-200 bg-amber-50 text-amber-900"
                : "border-green-200 bg-green-50 text-green-900"
          }`}
        >
          {report.sample_size.status === "INSUFFICIENT_DATA" ? (
            <AlertTriangle className="h-5 w-5 shrink-0 text-red-600 mt-0.5" />
          ) : report.sample_size.status === "DESCRIPTIVE_ONLY" ? (
            <Info className="h-5 w-5 shrink-0 text-amber-600 mt-0.5" />
          ) : (
            <CheckCircle2 className="h-5 w-5 shrink-0 text-green-600 mt-0.5" />
          )}
          <div>
            <div className="font-bold">
              {report.sample_size.status === "INSUFFICIENT_DATA"
                ? "INSUFFICIENT SAMPLE SIZE"
                : report.sample_size.status === "DESCRIPTIVE_ONLY"
                  ? "DESCRIPTIVE ONLY — LOW SAMPLE SIZE"
                  : "STATISTICALLY ADEQUATE SAMPLE"}
            </div>
            <div className="mt-0.5 leading-relaxed">{report.sample_size.message}</div>
          </div>
        </div>
      )}

      {/* KPI Cards Grid */}
      {report && (
        <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-5">
          <StatCard
            label="Evaluated Observations"
            value={report.sample_size.labeled_observations}
            sub={<>{report.sample_size.positive_outcomes} pos · {report.sample_size.negative_outcomes} neg · {report.sample_size.natural_recoveries_excluded} natural</>}
            tooltip="Cases with definitive ground-truth recovery labels. Natural recoveries are tracked separately."
            accent="border-l-slate-600"
            testId="kpi-observations"
          />

          <StatCard
            label="Precision"
            value={fmtPercent(report.classification_metrics.precision)}
            sub={<>TP / (TP + FP) at {Math.round(threshold * 100)}% threshold</>}
            tooltip="Of cases predicted recoverable, what percentage actually settled through verified recovery?"
            accent="border-l-blue-600"
            testId="kpi-precision"
          />

          <StatCard
            label="Recall (Sensitivity)"
            value={fmtPercent(report.classification_metrics.recall)}
            sub={<>TP / (TP + FN) at {Math.round(threshold * 100)}% threshold</>}
            tooltip="Of all verified recoverable cases, what percentage did the model correctly identify?"
            accent="border-l-blue-500"
            testId="kpi-recall"
          />

          <StatCard
            label="F1 Score"
            value={fmtFloat(report.classification_metrics.f1, 3)}
            sub={<>Harmonic mean of precision & recall</>}
            tooltip="Balanced measure of classification accuracy. Ranges from 0 (worst) to 1 (best)."
            accent="border-l-indigo-600"
            testId="kpi-f1"
          />

          <StatCard
            label="Brier Score"
            value={fmtFloat(report.calibration.brier_score, 4)}
            sub={<>Mean squared probability error (lower is better)</>}
            tooltip="Mean squared error between predicted probability and binary outcome. A lower score indicates better probability calibration."
            accent="border-l-emerald-600"
            testId="kpi-brier"
          />

          <StatCard
            label="Calibration Error (ECE)"
            value={fmtFloat(report.calibration.expected_calibration_error, 4)}
            sub={
              <div className="mt-1">
                <StatusBadge value={report.calibration.calibration_status} />
              </div>
            }
            tooltip="Expected Calibration Error across 10 probability buckets. Measures average gap between predicted likelihood and actual recovery rate."
            accent="border-l-amber-500"
            testId="kpi-ece"
          />
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-slate-200">
        <nav className="flex space-x-6 text-sm font-medium">
          {[
            { id: "performance", label: "Classification & Confusion Matrix" },
            { id: "calibration", label: "Reliability & Calibration" },
            { id: "thresholds", label: "Threshold Sweep & Curves" },
            { id: "actions", label: "Action Impact & Natural Baseline" },
            { id: "comparison", label: "Model Comparison" },
            { id: "runs", label: `Frozen Snapshots (${runs.length})` },
          ].map((tab) => (
            <button
              key={tab.id}
              data-testid={`tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`border-b-2 pb-3 transition-colors ${
                activeTab === tab.id
                  ? "border-blue-600 text-blue-600 font-semibold"
                  : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* TAB 1: Performance & Confusion Matrix */}
      {activeTab === "performance" && report && (
        <div className="space-y-6" data-testid="panel-performance">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Interactive Threshold Slider Card */}
            <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              <h3 className="font-heading text-base font-medium text-slate-900 flex items-center justify-between">
                <span>Decision Threshold</span>
                <span className="font-mono text-sm font-bold text-blue-600">{(threshold * 100).toFixed(0)}%</span>
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                Adjust the probability cutoff for predicting recoverable vs unrecoverable cases.
              </p>
              <div className="mt-6 space-y-4">
                <input
                  type="range"
                  min="0.0"
                  max="1.0"
                  step="0.05"
                  value={threshold}
                  onChange={(e) => setThreshold(parseFloat(e.target.value))}
                  data-testid="threshold-slider"
                  className="w-full accent-blue-600"
                />
                <div className="flex justify-between text-[11px] text-slate-400 font-mono">
                  <span>0% (All Positive)</span>
                  <span>50%</span>
                  <span>100% (All Negative)</span>
                </div>
              </div>

              <div className="mt-8 space-y-2 border-t border-slate-100 pt-4 text-xs">
                <div className="flex justify-between py-1">
                  <span className="text-slate-500">Accuracy</span>
                  <span className="font-semibold text-slate-900">{fmtPercent(report.classification_metrics.accuracy)}</span>
                </div>
                <div className="flex justify-between py-1">
                  <span className="text-slate-500">Specificity (TNR)</span>
                  <span className="font-semibold text-slate-900">{fmtPercent(report.classification_metrics.specificity)}</span>
                </div>
                <div className="flex justify-between py-1">
                  <span className="text-slate-500">Negative Predictive Value (NPV)</span>
                  <span className="font-semibold text-slate-900">{fmtPercent(report.classification_metrics.negative_predictive_value)}</span>
                </div>
                <div className="flex justify-between py-1">
                  <span className="text-slate-500">False Positive Rate (FPR)</span>
                  <span className="font-semibold text-slate-900">{fmtPercent(report.classification_metrics.false_positive_rate)}</span>
                </div>
              </div>
            </div>

            {/* Confusion Matrix Card */}
            <div className="lg:col-span-2 rounded-xl border border-slate-200 bg-white p-6 shadow-sm" data-testid="confusion-matrix-card">
              <h3 className="font-heading text-base font-medium text-slate-900">
                Confusion Matrix
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                Empirical distribution of true and false predictions at threshold = {(threshold * 100).toFixed(0)}%.
              </p>

              <div className="mt-6 grid grid-cols-2 gap-4 max-w-xl mx-auto">
                <div className="rounded-xl border border-green-200 bg-green-50/60 p-5 text-center" data-testid="cm-tp">
                  <div className="text-[11px] font-bold uppercase tracking-wider text-green-700">True Positive (TP)</div>
                  <div className="mt-2 text-3xl font-bold text-green-900">{report.confusion_matrix.tp}</div>
                  <div className="mt-1 text-[11px] text-green-600">Predicted recoverable, actually recovered</div>
                </div>

                <div className="rounded-xl border border-red-200 bg-red-50/60 p-5 text-center" data-testid="cm-fp">
                  <div className="text-[11px] font-bold uppercase tracking-wider text-red-700">False Positive (FP)</div>
                  <div className="mt-2 text-3xl font-bold text-red-900">{report.confusion_matrix.fp}</div>
                  <div className="mt-1 text-[11px] text-red-600">Predicted recoverable, failed to recover</div>
                </div>

                <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-5 text-center" data-testid="cm-fn">
                  <div className="text-[11px] font-bold uppercase tracking-wider text-amber-700">False Negative (FN)</div>
                  <div className="mt-2 text-3xl font-bold text-amber-900">{report.confusion_matrix.fn}</div>
                  <div className="mt-1 text-[11px] text-amber-600">Predicted unrecoverable, actually recovered</div>
                </div>

                <div className="rounded-xl border border-slate-200 bg-slate-50 p-5 text-center" data-testid="cm-tn">
                  <div className="text-[11px] font-bold uppercase tracking-wider text-slate-700">True Negative (TN)</div>
                  <div className="mt-2 text-3xl font-bold text-slate-900">{report.confusion_matrix.tn}</div>
                  <div className="mt-1 text-[11px] text-slate-600">Predicted unrecoverable, failed to recover</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: Calibration & Reliability Diagram */}
      {activeTab === "calibration" && report && (
        <div className="space-y-6" data-testid="panel-calibration">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 pb-4">
              <div>
                <h3 className="font-heading text-base font-medium text-slate-900">
                  Reliability Diagram (Calibration Curve)
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  Compares mean predicted probability against observed empirical recovery rate across 10 buckets. A perfectly calibrated model aligns with the diagonal dashed line.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500 font-medium">Status:</span>
                <StatusBadge value={report.calibration.calibration_status} />
              </div>
            </div>

            <div className="mt-6 grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Calibration Diagram Chart */}
              <div className="lg:col-span-2 h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={report.calibration.reliability_diagram} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="bin_label" tick={{ fontSize: 10 }} label={{ value: "Predicted Probability Bucket", position: "bottom", offset: 5, fontSize: 11 }} />
                    <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} label={{ value: "Observed Recovery Rate", angle: -90, position: "left", offset: 0, fontSize: 11 }} />
                    <Tooltip formatter={(val, name) => [val != null ? `${(val * 100).toFixed(1)}%` : "No data", name === "ideal" ? "Perfect Calibration" : "Observed Rate"]} />
                    <Line type="monotone" dataKey="ideal" stroke="#94a3b8" strokeDasharray="5 5" name="Perfect Calibration" dot={false} strokeWidth={1.5} />
                    <Line type="monotone" dataKey="observed_rate" stroke="#2563eb" name="Observed Recovery Rate" strokeWidth={2.5} connectNulls dot={{ r: 4, fill: "#2563eb" }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Bucket Breakdown Table */}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-slate-200 text-slate-500 font-semibold">
                      <th className="py-2">Bucket</th>
                      <th className="py-2 text-right">Count</th>
                      <th className="py-2 text-right">Observed</th>
                      <th className="py-2 text-right">Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.calibration.reliability_diagram.map((b) => (
                      <tr key={b.bin_index} className="border-b border-slate-100">
                        <td className="py-1.5 font-mono text-slate-700">{b.bin_label}</td>
                        <td className="py-1.5 text-right font-medium text-slate-900">{b.count}</td>
                        <td className="py-1.5 text-right font-mono text-slate-600">{b.observed_rate != null ? fmtPercent(b.observed_rate) : "—"}</td>
                        <td className="py-1.5 text-right font-mono text-amber-700">{b.count > 0 ? fmtPercent(b.calibration_gap) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: Threshold Analysis & Curves */}
      {activeTab === "thresholds" && report && (
        <div className="space-y-6" data-testid="panel-thresholds">
          {/* Threshold Sweep Chart */}
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 pb-4">
              <div>
                <h3 className="font-heading text-base font-medium text-slate-900">
                  Precision-Recall-F1 Threshold Sweep
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  Evaluates metric trade-offs across cutoffs from 0.00 to 1.00. Optimal F1 threshold is highlighted.
                </p>
              </div>
              <div className="rounded-lg bg-blue-50 border border-blue-200 px-3 py-1.5 text-xs text-blue-800">
                Optimal F1 Cutoff: <span className="font-mono font-bold">{(report.threshold_analysis.optimal_f1_threshold * 100).toFixed(0)}%</span> (Max F1: {report.threshold_analysis.max_f1_score})
              </div>
            </div>

            <div className="mt-6 h-72">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={report.threshold_analysis.sweep_points} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="threshold" tick={{ fontSize: 10 }} label={{ value: "Decision Threshold", position: "bottom", offset: 5, fontSize: 11 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(val, name) => [val != null ? `${(val * 100).toFixed(1)}%` : "—", name]} />
                  <ReferenceLine x={report.threshold_analysis.optimal_f1_threshold} stroke="#ea580c" strokeDasharray="3 3" label={{ value: "Optimal F1", fill: "#ea580c", fontSize: 10 }} />
                  <Line type="monotone" dataKey="precision" stroke="#2563eb" name="Precision" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="recall" stroke="#16a34a" name="Recall" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="f1" stroke="#ea580c" name="F1 Score" strokeWidth={2.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ROC & PR Curves Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* ROC Curve */}
            <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              <h3 className="font-heading text-base font-medium text-slate-900 flex items-center justify-between">
                <span>ROC Curve</span>
                <span className="font-mono text-xs font-bold text-slate-600">
                  ROC-AUC: {report.curves.roc_auc != null ? report.curves.roc_auc : "N/A"}
                </span>
              </h3>
              <p className="mt-1 text-xs text-slate-500">True Positive Rate vs False Positive Rate</p>
              <div className="mt-4 h-64">
                {report.curves.roc_available ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={report.curves.roc_curve} margin={{ top: 10, right: 10, bottom: 20, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                      <XAxis dataKey="fpr" tick={{ fontSize: 10 }} label={{ value: "FPR", position: "bottom", offset: 5, fontSize: 10 }} />
                      <YAxis dataKey="tpr" domain={[0, 1]} tick={{ fontSize: 10 }} label={{ value: "TPR", angle: -90, position: "left", offset: 5, fontSize: 10 }} />
                      <Tooltip />
                      <Line type="monotone" dataKey="tpr" stroke="#2563eb" strokeWidth={2} dot={false} name="TPR" />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-lg bg-slate-50 border border-dashed border-slate-200 text-xs text-slate-400 p-4 text-center">
                    {report.curves.reason || "ROC curve unavailable due to insufficient class diversity."}
                  </div>
                )}
              </div>
            </div>

            {/* Precision-Recall Curve */}
            <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
              <h3 className="font-heading text-base font-medium text-slate-900 flex items-center justify-between">
                <span>Precision-Recall Curve</span>
                <span className="font-mono text-xs font-bold text-slate-600">
                  PR-AUC: {report.curves.pr_auc != null ? report.curves.pr_auc : "N/A"}
                </span>
              </h3>
              <p className="mt-1 text-xs text-slate-500">Precision vs Recall across thresholds</p>
              <div className="mt-4 h-64">
                {report.curves.pr_available ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={report.curves.pr_curve} margin={{ top: 10, right: 10, bottom: 20, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                      <XAxis dataKey="recall" tick={{ fontSize: 10 }} label={{ value: "Recall", position: "bottom", offset: 5, fontSize: 10 }} />
                      <YAxis dataKey="precision" domain={[0, 1]} tick={{ fontSize: 10 }} label={{ value: "Precision", angle: -90, position: "left", offset: 5, fontSize: 10 }} />
                      <Tooltip />
                      <Line type="monotone" dataKey="precision" stroke="#16a34a" strokeWidth={2} dot={false} name="Precision" />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center justify-center rounded-lg bg-slate-50 border border-dashed border-slate-200 text-xs text-slate-400 p-4 text-center">
                    {report.curves.reason || "PR curve unavailable due to insufficient class diversity."}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: Action Impact & Natural Baseline */}
      {activeTab === "actions" && report && (
        <div className="space-y-6" data-testid="panel-actions">
          {/* Natural Baseline Card */}
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="font-heading text-base font-medium text-slate-900">
              Natural Recovery Baseline
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              Measures customer settlements that occurred naturally without system action assistance.
            </p>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="rounded-lg bg-slate-50 p-4 border border-slate-100">
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Total Cases</div>
                <div className="mt-1 text-2xl font-bold text-slate-900">{report.natural_recovery_baseline.total_eligible_cases}</div>
              </div>
              <div className="rounded-lg bg-emerald-50 p-4 border border-emerald-100">
                <div className="text-[11px] font-bold uppercase tracking-wider text-emerald-700">Natural Recoveries</div>
                <div className="mt-1 text-2xl font-bold text-emerald-900">{report.natural_recovery_baseline.natural_recoveries}</div>
              </div>
              <div className="rounded-lg bg-blue-50 p-4 border border-blue-100">
                <div className="text-[11px] font-bold uppercase tracking-wider text-blue-700">Action-Assisted</div>
                <div className="mt-1 text-2xl font-bold text-blue-900">{report.natural_recovery_baseline.action_assisted_recoveries}</div>
              </div>
              <div className="rounded-lg bg-slate-50 p-4 border border-slate-100">
                <div className="text-[11px] font-bold uppercase tracking-wider text-slate-500">Natural Baseline Rate</div>
                <div className="mt-1 text-2xl font-bold text-slate-900">{fmtPercent(report.natural_recovery_baseline.natural_recovery_baseline_rate)}</div>
              </div>
            </div>
            <div className="mt-4 rounded-lg bg-slate-50 border border-slate-200 p-3 text-xs text-slate-500 font-mono">
              {report.natural_recovery_baseline.disclaimer}
            </div>
          </div>

          {/* Action Breakdown Table */}
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="font-heading text-base font-medium text-slate-900">
              Recovery Action Performance Breakdown
            </h3>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500 font-semibold uppercase tracking-wider">
                    <th className="py-2.5">Action</th>
                    <th className="py-2.5 text-right">Selected</th>
                    <th className="py-2.5 text-right">Verified Recovered</th>
                    <th className="py-2.5 text-right">Action-Assisted</th>
                    <th className="py-2.5 text-right">Recovery Rate</th>
                    <th className="py-2.5 text-right">Mean Likelihood</th>
                    <th className="py-2.5 text-right">Verified Amount</th>
                    <th className="py-2.5 text-right">Incremental Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {report.action_performance.map((a) => (
                    <tr key={a.action_type} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="py-3 font-medium text-slate-900">{a.action_type.replace(/_/g, " ")}</td>
                      <td className="py-3 text-right tabular-nums text-slate-700">{a.total_cases}</td>
                      <td className="py-3 text-right tabular-nums text-green-700 font-semibold">{a.verified_recovered_count}</td>
                      <td className="py-3 text-right tabular-nums text-blue-700 font-semibold">{a.action_assisted_count}</td>
                      <td className="py-3 text-right tabular-nums text-slate-900 font-medium">{fmtPercent(a.recovery_rate)}</td>
                      <td className="py-3 text-right tabular-nums text-slate-600 font-mono">{fmtPercent(a.mean_predicted_likelihood)}</td>
                      <td className="py-3 text-right tabular-nums text-slate-900">{formatMoney(a.gross_recovered_amount, "INR")}</td>
                      <td className="py-3 text-right tabular-nums text-emerald-700 font-medium">{formatMoney(a.incremental_recovered_amount, "INR")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* TAB 5: Model Comparison */}
      {activeTab === "comparison" && (
        <div className="space-y-6" data-testid="panel-comparison">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="font-heading text-base font-medium text-slate-900">
              Model Version Comparison
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              Compare accuracy, Brier score, and calibration metrics side-by-side on identical historical cohorts.
            </p>

            <div className="mt-4 flex flex-wrap items-center gap-4 border-b border-slate-100 pb-4">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-600">Model A:</span>
                <select
                  value={compModelA}
                  onChange={(e) => setCompModelA(e.target.value)}
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 outline-none"
                >
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
                  <option value="heuristic-fallback-v1">heuristic-fallback-v1</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-slate-600">Model B:</span>
                <select
                  value={compModelB}
                  onChange={(e) => setCompModelB(e.target.value)}
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 outline-none"
                >
                  <option value="heuristic-fallback-v1">heuristic-fallback-v1</option>
                  <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
                </select>
              </div>

              <button
                onClick={handleCompare}
                disabled={comparing}
                data-testid="compare-models-btn"
                className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {comparing ? "Comparing…" : "Compare Models"}
              </button>
            </div>

            {compResult && (
              <div className="mt-6 overflow-x-auto">
                <table className="w-full text-left text-xs" data-testid="model-comparison-table">
                  <thead>
                    <tr className="border-b border-slate-200 text-slate-500 font-semibold uppercase">
                      <th className="py-2.5">Evaluation Metric</th>
                      <th className="py-2.5 font-bold text-blue-900">{compResult.model_a.name}</th>
                      <th className="py-2.5 font-bold text-indigo-900">{compResult.model_b.name}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Labeled Observations</td>
                      <td className="py-2.5 font-mono">{compResult.model_a.sample_size.labeled_observations}</td>
                      <td className="py-2.5 font-mono">{compResult.model_b.sample_size.labeled_observations}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Precision</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtPercent(compResult.model_a.metrics.precision)}</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtPercent(compResult.model_b.metrics.precision)}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Recall</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtPercent(compResult.model_a.metrics.recall)}</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtPercent(compResult.model_b.metrics.recall)}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">F1 Score</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtFloat(compResult.model_a.metrics.f1, 3)}</td>
                      <td className="py-2.5 font-mono font-semibold">{fmtFloat(compResult.model_b.metrics.f1, 3)}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Brier Score (lower is better)</td>
                      <td className="py-2.5 font-mono text-emerald-700">{fmtFloat(compResult.model_a.brier_score, 4)}</td>
                      <td className="py-2.5 font-mono text-emerald-700">{fmtFloat(compResult.model_b.brier_score, 4)}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Expected Calibration Error (ECE)</td>
                      <td className="py-2.5 font-mono">{fmtFloat(compResult.model_a.expected_calibration_error, 4)}</td>
                      <td className="py-2.5 font-mono">{fmtFloat(compResult.model_b.expected_calibration_error, 4)}</td>
                    </tr>
                    <tr>
                      <td className="py-2.5 font-medium text-slate-700">Calibration Status</td>
                      <td className="py-2.5"><StatusBadge value={compResult.model_a.calibration_status} /></td>
                      <td className="py-2.5"><StatusBadge value={compResult.model_b.calibration_status} /></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 6: Frozen Evaluation Runs */}
      {activeTab === "runs" && (
        <div className="space-y-6" data-testid="panel-runs">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="font-heading text-base font-medium text-slate-900">
              Frozen Evaluation Snapshots
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              Immutable historical runs preserved with their complete frozen datasets for audit and reproducibility.
            </p>

            <div className="mt-6 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-slate-500 font-semibold uppercase">
                    <th className="py-2.5">Snapshot Title</th>
                    <th className="py-2.5">Cohort</th>
                    <th className="py-2.5 text-right">Threshold</th>
                    <th className="py-2.5 text-right">Samples</th>
                    <th className="py-2.5 text-right">F1 Score</th>
                    <th className="py-2.5 text-right">Brier Score</th>
                    <th className="py-2.5">Calibration Status</th>
                    <th className="py-2.5">Created</th>
                    <th className="py-2.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {runs.map((r) => (
                    <tr key={r.run_id} className="hover:bg-slate-50">
                      <td className="py-3 font-medium text-slate-900">{r.title || r.run_id}</td>
                      <td className="py-3"><StatusBadge value={r.cohort} /></td>
                      <td className="py-3 text-right font-mono">{fmtPercent(r.threshold)}</td>
                      <td className="py-3 text-right font-mono text-slate-700">{r.report?.sample_size?.labeled_observations || r.snapshot_case_count}</td>
                      <td className="py-3 text-right font-mono font-semibold text-slate-900">{fmtFloat(r.report?.classification_metrics?.f1, 3)}</td>
                      <td className="py-3 text-right font-mono text-emerald-700">{fmtFloat(r.report?.calibration?.brier_score, 4)}</td>
                      <td className="py-3"><StatusBadge value={r.report?.calibration?.calibration_status} /></td>
                      <td className="py-3 text-slate-500 font-mono text-[11px]">{new Date(r.created_at).toLocaleString("en-GB")}</td>
                      <td className="py-3 text-right">
                        <button
                          onClick={() => handleDeleteRun(r.run_id)}
                          data-testid={`delete-run-${r.run_id}`}
                          className="rounded p-1 text-slate-400 hover:text-red-600 hover:bg-red-50"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {runs.length === 0 && (
                    <tr>
                      <td colSpan={9} className="py-8 text-center text-slate-400">
                        No frozen evaluation snapshots yet. Click "Freeze Evaluation Snapshot" above to create one.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Freeze Snapshot Modal */}
      {runModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" data-testid="freeze-run-modal">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl border border-slate-200">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 className="font-heading text-base font-medium text-slate-900">Freeze Evaluation Snapshot</h3>
              <button onClick={() => setRunModalOpen(false)} className="text-slate-400 hover:text-slate-600">
                <X className="h-4 w-4" />
              </button>
            </div>
            <form onSubmit={handleCreateRun} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-600">Snapshot Title</label>
                <input
                  type="text"
                  placeholder={`Evaluation Snapshot: ${selectedCohort}`}
                  value={runTitle}
                  onChange={(e) => setRunTitle(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600">Cohort & Threshold</label>
                <div className="mt-1 flex items-center gap-2 text-xs font-mono text-slate-600 bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                  <span>{selectedCohort}</span> · <span>Cutoff: {(threshold * 100).toFixed(0)}%</span>
                </div>
              </div>
              <div>
                <label className="block text-xs font-semibold text-slate-600">Notes (Optional)</label>
                <textarea
                  placeholder="e.g. Pre-deployment baseline test"
                  value={runNotes}
                  onChange={(e) => setRunNotes(e.target.value)}
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none focus:border-blue-500"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setRunModalOpen(false)}
                  className="rounded-lg border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={savingRun}
                  data-testid="submit-freeze-run-btn"
                  className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {savingRun ? "Freezing…" : "Save Snapshot"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
