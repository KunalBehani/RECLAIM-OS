import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import api from "../api";
import PageHeader from "../components/PageHeader";
import StatusBadge from "../components/StatusBadge";

const REQUIRED_FIELDS = new Set(["amount", "status"]);
const LINKAGE_FIELDS = ["order_id", "invoice_id"];
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 180000;

export default function Ingest() {
  const navigate = useNavigate();
  const fileRef = useRef(null);
  const pollTimer = useRef(null);
  const [upload, setUpload] = useState(null);
  const [sheet, setSheet] = useState(null);
  const [mapping, setMapping] = useState({});
  const [report, setReport] = useState(null);
  const [importing, setImporting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [batches, setBatches] = useState([]);

  const loadBatches = () => api.get("/ingest/batches").then((res) => setBatches(res.data.batches)).catch(() => {});
  useEffect(() => { loadBatches(); }, []);
  useEffect(() => () => { if (pollTimer.current) clearInterval(pollTimer.current); }, []);

  const handleFile = async (file) => {
    if (!file) return;
    setUploading(true);
    setReport(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.post("/ingest/upload", form, { headers: { "Content-Type": "multipart/form-data" } });
      setUpload(res.data);
      setSheet(res.data.default_sheet);
      const initial = {};
      Object.entries(res.data.suggested_mapping).forEach(([field, meta]) => {
        initial[field] = meta.header || "";
      });
      setMapping(initial);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const pollBatch = (batchId) => {
    const started = Date.now();
    pollTimer.current = setInterval(async () => {
      try {
        const res = await api.get(`/ingest/${batchId}`);
        if (res.data.status === "IMPORTED") {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
          setReport({ report: res.data.report, import_results: res.data.import_results });
          setImporting(false);
          loadBatches();
          toast.success("Batch imported through the recovery engine");
        } else if (res.data.status === "IMPORT_FAILED") {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
          setImporting(false);
          toast.error(`Import failed: ${res.data.error || "unknown error"}`);
        } else if (Date.now() - started > POLL_TIMEOUT_MS) {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
          setImporting(false);
          toast.error("Import is taking unusually long — check batch history below.");
        }
      } catch {
        // transient poll failure — keep polling
      }
    }, POLL_INTERVAL_MS);
  };

  const confirmImport = async () => {
    if (!mapping.amount || !mapping.status || (!mapping.order_id && !mapping.invoice_id)) {
      toast.error("Map amount, status, and at least one of order_id / invoice_id before importing.");
      return;
    }
    setImporting(true);
    try {
      const payload = { sheet, mapping: Object.fromEntries(Object.entries(mapping).map(([k, v]) => [k, v || null])) };
      const res = await api.post(`/ingest/${upload.batch_id}/confirm`, payload);
      if (res.status === 202) {
        toast.info("Import accepted — processing in the background…");
        pollBatch(upload.batch_id);
        return;
      }
      setReport(res.data);
      setImporting(false);
      loadBatches();
    } catch (e) {
      if (e.response?.status === 409) {
        toast.info("Batch is already importing or imported — fetching its status…");
        pollBatch(upload.batch_id);
        return;
      }
      setImporting(false);
      toast.error(e.response?.data?.detail || "Import failed");
    }
  };

  const currentSheet = upload?.sheets.find((s) => s.name === sheet);

  return (
    <div className="space-y-10" data-testid="ingest-page">
      <PageHeader eyebrow="Recovery" title="Batch Data Ingestion"
        subtitle="Upload CSV or Excel merchant data. Nothing enters financial totals until you review the field mapping and the validation report." />

      {!upload && (
        <button
          data-testid="upload-dropzone"
          onClick={() => fileRef.current?.click()}
          className="flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 p-16 text-center transition-colors duration-200 hover:border-slate-400 hover:bg-slate-100"
        >
          <UploadCloud className="h-10 w-10 text-slate-400" />
          <p className="mt-4 text-sm font-medium text-slate-700">{uploading ? "Inspecting file…" : "Click to upload CSV, XLSX or XLS"}</p>
          <p className="mt-1 text-xs text-slate-400">Max 5 MB. Headers are auto-detected; Claude assists with ambiguous column mapping.</p>
          <input
            ref={fileRef}
            data-testid="upload-file-input"
            type="file"
            accept=".csv,.xlsx,.xls"
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </button>
      )}

      {upload && !report && (
        <section className="rounded-xl border border-slate-200 bg-white p-6" data-testid="mapping-review">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <FileSpreadsheet className="h-5 w-5 text-slate-400" />
              <div>
                <div className="text-sm font-medium text-slate-900">{upload.filename}</div>
                <div className="text-xs text-slate-500">{currentSheet?.row_count} rows · {currentSheet?.headers.length} columns detected</div>
              </div>
            </div>
            {upload.sheets.length > 1 && (
              <select data-testid="sheet-select" value={sheet} onChange={(e) => setSheet(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none">
                {upload.sheets.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.row_count} rows)</option>)}
              </select>
            )}
          </div>

          <div className="mt-6 overflow-x-auto">
            <table className="w-full border-collapse text-sm" data-testid="mapping-table">
              <thead>
                <tr className="text-left text-xs font-bold uppercase tracking-wider text-slate-500">
                  <th className="border border-slate-200 bg-slate-50 px-4 py-2">Target field</th>
                  <th className="border border-slate-200 bg-slate-50 px-4 py-2">Maps to column</th>
                  <th className="border border-slate-200 bg-slate-50 px-4 py-2">Confidence</th>
                  <th className="border border-slate-200 bg-slate-50 px-4 py-2">Source</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(upload.suggested_mapping).map(([field, meta]) => (
                  <tr key={field}>
                    <td className="border border-slate-200 px-4 py-2">
                      <span className="font-mono text-xs font-medium text-slate-800">{field}</span>
                      {REQUIRED_FIELDS.has(field) && <span className="ml-2 rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-bold text-red-600 border border-red-200">REQUIRED</span>}
                      {LINKAGE_FIELDS.includes(field) && <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 border border-amber-200">LINKAGE</span>}
                    </td>
                    <td className="border border-slate-200 px-4 py-2">
                      <select
                        data-testid={`mapping-select-${field}`}
                        value={mapping[field] || ""}
                        onChange={(e) => setMapping((m) => ({ ...m, [field]: e.target.value }))}
                        className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs outline-none"
                      >
                        <option value="">— not mapped —</option>
                        {currentSheet?.headers.map((h) => <option key={h} value={h}>{h}</option>)}
                      </select>
                    </td>
                    <td className="border border-slate-200 px-4 py-2 tabular-nums text-xs text-slate-600">
                      {meta.header ? `${Math.round(meta.confidence * 100)}%` : "—"}
                    </td>
                    <td className="border border-slate-200 px-4 py-2">
                      {meta.source === "ai" ? (
                        <span className="rounded bg-blue-50 px-2 py-0.5 text-[10px] font-bold text-blue-700 border border-blue-200">AI SUGGESTED</span>
                      ) : meta.source === "heuristic" ? (
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600 border border-slate-200">RULE MATCH</span>
                      ) : (
                        <span className="text-xs text-slate-400">unmapped</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {currentSheet?.sample_rows?.length > 0 && (
            <div className="mt-6">
              <div className="text-xs font-bold uppercase tracking-[0.2em] text-slate-500">Sample rows</div>
              <div className="mt-2 overflow-x-auto rounded-lg border border-slate-200">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50">
                      {currentSheet.headers.map((h) => <th key={h} className="border-b border-slate-200 px-3 py-2 text-left font-mono font-medium text-slate-600">{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {currentSheet.sample_rows.map((row, i) => (
                      <tr key={i} className="border-b border-slate-100">
                        {currentSheet.headers.map((h) => <td key={h} className="px-3 py-2 font-mono text-slate-500">{row[h] ?? ""}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="mt-6 flex items-center gap-3">
            <button data-testid="mapping-confirm-btn" onClick={confirmImport} disabled={importing}
              className="flex items-center gap-2 rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-slate-800 disabled:opacity-50">
              {importing && <Loader2 className="h-4 w-4 animate-spin" />}
              {importing ? "Importing in background — polling for results…" : "Validate & import batch"}
            </button>
            <button data-testid="upload-reset-btn" onClick={() => { setUpload(null); setReport(null); setImporting(false); }}
              className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-600 transition-colors duration-200 hover:bg-slate-50">
              Cancel
            </button>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            Critical financial fields are never silently guessed — low-confidence mappings require your confirmation here.
            Imports run in the background; AI analysis uses a bounded time budget per batch and falls back to the deterministic engine beyond it.
          </p>
        </section>
      )}

      {report && (
        <section className="space-y-6" data-testid="validation-report">
          <div className="rounded-xl border border-slate-200 bg-white p-6">
            <h2 className="font-heading text-lg font-medium text-slate-900">Data Quality Report</h2>
            <div className="mt-4 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200">
              {[
                ["Total rows", report.report.total_rows],
                ["Valid", report.report.valid_rows],
                ["Invalid", report.report.invalid_rows],
                ["Duplicates", report.report.duplicate_rows],
                ["Invalid amounts", report.report.invalid_amounts],
                ["Invalid dates", report.report.invalid_dates],
                ["Unsupported status", report.report.unsupported_statuses],
                ["Missing linkage", report.report.missing_linkage],
              ].map(([label, value]) => (
                <div key={label} className="bg-white p-4">
                  <div className="text-xl font-semibold tabular-nums text-slate-900">{value}</div>
                  <div className="mt-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</div>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4">
              <span className="text-sm font-medium text-red-700">{report.report.rows_to_exception_queue} rows sent to the exception queue</span>
              <span className="ml-2 text-xs text-red-500">— excluded from all financial calculations</span>
            </div>
            {report.report.row_errors?.length > 0 && (
              <div className="mt-4 max-h-48 overflow-auto rounded-lg border border-slate-200">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="bg-slate-50 text-left">
                      <th className="border-b border-slate-200 px-3 py-2 font-bold uppercase tracking-wider text-slate-500">Row</th>
                      <th className="border-b border-slate-200 px-3 py-2 font-bold uppercase tracking-wider text-slate-500">Errors</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.report.row_errors.map((e, i) => (
                      <tr key={i} className="border-b border-slate-100">
                        <td className="px-3 py-1.5 tabular-nums text-slate-600">{e.row}</td>
                        <td className="px-3 py-1.5 font-mono text-red-600">{e.errors.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-6" data-testid="import-results">
            <h2 className="font-heading text-lg font-medium text-slate-900">Recovery Engine Results</h2>
            <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                ["Cases created", report.import_results.cases_created],
                ["Existing cases updated", report.import_results.cases_updated],
                ["Naturally recovered (no case)", report.import_results.naturally_recovered],
                ["Duplicates blocked", report.import_results.duplicates_blocked],
                ["Verified recovered", report.import_results.verified_recovered],
                ["Closed natural", report.import_results.closed_natural],
                ["Payments recorded", report.import_results.payments_recorded],
                ["Exceptions", report.import_results.exceptions],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-slate-100 bg-slate-50 p-4">
                  <div className="text-xl font-semibold tabular-nums text-slate-900">{value}</div>
                  <div className="mt-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">{label}</div>
                </div>
              ))}
            </div>
            <div className="mt-3 text-xs text-slate-500">
              Analysis engine: {report.import_results.llm_analyses} cases via Claude Sonnet · {report.import_results.heuristic_fallbacks} via deterministic fallback (bounded per-batch AI budget)
            </div>
            <div className="mt-6 flex gap-3">
              <button data-testid="view-dashboard-btn" onClick={() => navigate("/")}
                className="rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white transition-colors duration-200 hover:bg-slate-800">
                View dashboard
              </button>
              <button data-testid="upload-another-btn" onClick={() => { setUpload(null); setReport(null); }}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-600 transition-colors duration-200 hover:bg-slate-50">
                Upload another file
              </button>
            </div>
          </div>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white" data-testid="batch-history">
        <div className="border-b border-slate-100 p-6">
          <h2 className="font-heading text-lg font-medium text-slate-900">Recent Batches</h2>
        </div>
        <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-left text-xs font-bold uppercase tracking-wider text-slate-500">
              <th className="px-6 py-3">File</th>
              <th className="px-6 py-3">Status</th>
              <th className="px-6 py-3 text-right">Valid / Total</th>
              <th className="px-6 py-3 text-right">Cases created</th>
              <th className="px-6 py-3">Uploaded</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.batch_id} className="border-b border-slate-100">
                <td className="px-6 py-3 font-mono text-xs text-slate-700">{b.filename}</td>
                <td className="px-6 py-3"><StatusBadge value={b.status} /></td>
                <td className="px-6 py-3 text-right tabular-nums text-slate-600">
                  {b.report ? `${b.report.valid_rows} / ${b.report.total_rows}` : "—"}
                </td>
                <td className="px-6 py-3 text-right tabular-nums text-slate-600">{b.import_results?.cases_created ?? "—"}</td>
                <td className="px-6 py-3 text-xs text-slate-500">{new Date(b.created_at).toLocaleString("en-GB")}</td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr><td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-400">No batches uploaded yet.</td></tr>
            )}
          </tbody>
        </table>
        </div>
      </section>
    </div>
  );
}
