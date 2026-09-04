const STATUS_STYLES = {
  OPEN: "bg-slate-100 text-slate-700 border-slate-200",
  EVALUATED: "bg-blue-50 text-blue-700 border-blue-200",
  APPROVAL_PENDING: "bg-orange-50 text-orange-700 border-orange-200",
  ACTION_SCHEDULED: "bg-blue-50 text-blue-700 border-blue-200",
  ACTION_EXECUTED: "bg-blue-50 text-blue-700 border-blue-200",
  VERIFYING: "bg-blue-50 text-blue-700 border-blue-200",
  VERIFIED_RECOVERED: "bg-green-50 text-green-700 border-green-200",
  NATURALLY_RECOVERED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  NOT_RECOVERED: "bg-red-50 text-red-700 border-red-200",
  STOPPED: "bg-slate-100 text-slate-600 border-slate-200",
  INVALID: "bg-slate-100 text-slate-500 border-slate-200",
  PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  UNVERIFIED: "bg-slate-100 text-slate-600 border-slate-200",
  VERIFIED: "bg-green-50 text-green-700 border-green-200",
  ALLOW: "bg-green-50 text-green-700 border-green-200",
  BLOCK: "bg-red-50 text-red-700 border-red-200",
  APPROVAL: "bg-orange-50 text-orange-700 border-orange-200",
  STOP: "bg-slate-100 text-slate-600 border-slate-200",
  SIMULATED: "bg-violet-50 text-violet-700 border-violet-200",
  TEST_MODE: "bg-violet-50 text-violet-700 border-violet-200",
  WEBHOOK: "bg-blue-50 text-blue-700 border-blue-200",
  CSV_UPLOAD: "bg-slate-100 text-slate-700 border-slate-200",
  XLSX_UPLOAD: "bg-slate-100 text-slate-700 border-slate-200",
  EXECUTED: "bg-blue-50 text-blue-700 border-blue-200",
  BLOCKED: "bg-red-50 text-red-700 border-red-200",
  REJECTED: "bg-red-50 text-red-700 border-red-200",
  AWAITING_APPROVAL: "bg-orange-50 text-orange-700 border-orange-200",
  AWAITING_HUMAN: "bg-orange-50 text-orange-700 border-orange-200",
  NONE: "bg-slate-100 text-slate-500 border-slate-200",
  CLOSED: "bg-slate-100 text-slate-600 border-slate-200",
  HUMAN_APPROVED: "bg-green-50 text-green-700 border-green-200",
  AUTO_APPROVED: "bg-green-50 text-green-700 border-green-200",
  MANUAL_TRIGGER: "bg-blue-50 text-blue-700 border-blue-200",
  INVALID_CASE: "bg-slate-100 text-slate-500 border-slate-200",
  IMPORTED: "bg-green-50 text-green-700 border-green-200",
  IMPORTING: "bg-blue-50 text-blue-700 border-blue-200",
  IMPORT_FAILED: "bg-red-50 text-red-700 border-red-200",
  MAPPING_REVIEW: "bg-amber-50 text-amber-700 border-amber-200",
  LIVE: "bg-blue-50 text-blue-700 border-blue-200",
  IMPORTED: "bg-slate-100 text-slate-700 border-slate-200",
  PARTIALLY_RECOVERED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  CONNECTED: "bg-green-50 text-green-700 border-green-200",
  NOT_CONNECTED: "bg-slate-100 text-slate-600 border-slate-200",
  NOT_CONFIGURED: "bg-slate-100 text-slate-500 border-slate-200",
  ERROR: "bg-red-50 text-red-700 border-red-200",
  PROCESSED: "bg-green-50 text-green-700 border-green-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
  RECEIVED: "bg-blue-50 text-blue-700 border-blue-200",
  IGNORED_UNSUPPORTED: "bg-slate-100 text-slate-600 border-slate-200",
  LAB: "bg-amber-50 text-amber-800 border-amber-300 font-semibold",
  WELL_CALIBRATED: "bg-green-50 text-green-700 border-green-200",
  PARTIALLY_CALIBRATED: "bg-amber-50 text-amber-700 border-amber-200",
  POORLY_CALIBRATED: "bg-red-50 text-red-700 border-red-200",
  INSUFFICIENT_DATA: "bg-slate-100 text-slate-600 border-slate-200",
  DESCRIPTIVE_ONLY: "bg-amber-50 text-amber-700 border-amber-200",
  ADEQUATE: "bg-green-50 text-green-700 border-green-200",
};

export default function StatusBadge({ value, className = "" }) {
  if (!value) return <span className="text-slate-400">—</span>;
  const styles = STATUS_STYLES[value] || "bg-slate-100 text-slate-600 border-slate-200";
  return (
    <span
      data-testid={`badge-${String(value).toLowerCase().replace(/_/g, "-")}`}
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${styles} ${className}`}
    >
      {String(value).replace(/_/g, " ")}
    </span>
  );
}
