import io
import json
import os
import re
import uuid

import pandas as pd

from constants import normalize_status, now_iso

TARGET_FIELDS = [
    "payment_id",
    "order_id",
    "invoice_id",
    "customer_reference",
    "amount",
    "currency",
    "status",
    "failure_code",
    "failure_reason",
    "payment_method",
    "timestamp",
]

FIELD_SYNONYMS = {
    "payment_id": ["payment_id", "transaction_id", "txn_id", "paymentid", "transactionid", "reference_id", "ref_id", "payment_reference"],
    "order_id": ["order_id", "orderid", "order", "order_number", "order_ref", "order_reference"],
    "invoice_id": ["invoice_id", "invoiceid", "invoice", "invoice_number", "inv_id", "inv_no"],
    "customer_reference": ["customer_id", "customer", "customer_reference", "customer_ref", "email", "customer_email", "client_id"],
    "amount": ["amount", "payment_amount", "total", "value", "amount_paid", "grand_total", "price", "txn_amount"],
    "currency": ["currency", "currency_code", "ccy"],
    "status": ["status", "payment_status", "state", "txn_status", "transaction_status"],
    "failure_code": ["failure_code", "error_code", "decline_code", "code", "failure_reason_code"],
    "failure_reason": ["failure_reason", "error_message", "reason", "decline_reason", "error_description"],
    "payment_method": ["payment_method", "method", "payment_type", "card_type", "payment_mode"],
    "timestamp": ["timestamp", "created_at", "payment_time", "date", "payment_date", "txn_time", "transaction_date", "created", "time"],
}

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_STORED_ROWS = 5000


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def suggest_mapping(headers: list) -> dict:
    """Deterministic header matching against known synonyms. Never guesses
    critical financial fields below confidence 0.7."""
    normalized = {_norm_header(h): h for h in headers}
    mapping = {}
    for field in TARGET_FIELDS:
        best, confidence = None, 0.0
        for synonym in FIELD_SYNONYMS[field]:
            sn = _norm_header(synonym)
            if sn in normalized:
                best, confidence = normalized[sn], 0.95
                break
        if not best:
            for nh, original in normalized.items():
                for synonym in FIELD_SYNONYMS[field]:
                    sn = _norm_header(synonym)
                    if len(sn) >= 4 and (nh.startswith(sn) or sn.startswith(nh)):
                        best, confidence = original, 0.7
                        break
                if best:
                    break
        mapping[field] = {"header": best, "confidence": confidence, "source": "heuristic" if best else None}
    return mapping


async def ai_enhanced_mapping(headers: list, sample_rows: list, base_mapping: dict) -> dict:
    """Claude-assisted schema suggestion for fields the deterministic matcher
    could not map confidently. Falls back silently to heuristics."""
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return base_mapping
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return base_mapping

    unmapped = [f for f in TARGET_FIELDS if not base_mapping[f]["header"]]
    if not unmapped:
        return base_mapping

    system = (
        "You map merchant spreadsheet columns to a canonical payment schema for a revenue recovery system. "
        "Respond with ONLY valid JSON. Never guess critical financial fields (amount, status, timestamp) "
        "without clear evidence in the header name or sample values."
    )
    prompt = f"""Spreadsheet headers: {json.dumps(headers)}
Sample rows (up to 3): {json.dumps(sample_rows[:3], default=str)}
Canonical target fields still unmapped: {json.dumps(unmapped)}

Return JSON: {{"mapping": {{"<target_field>": "<exact header name or null>"}}, "confidence": {{"<target_field>": 0.0}}, "notes": "short note"}}
Only include target fields you can map with confidence >= 0.6. Use the exact header strings provided."""

    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"reclaim-mapping-{uuid.uuid4().hex[:12]}",
            system_message=system,
        ).with_model("anthropic", "claude-sonnet-4-6")
        raw = await chat.send_message(UserMessage(text=prompt))
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
        ai_map = data.get("mapping") or {}
        ai_conf = data.get("confidence") or {}
        for field in unmapped:
            header = ai_map.get(field)
            conf = float(ai_conf.get(field) or 0)
            if header and header in headers and conf >= 0.6:
                base_mapping[field] = {"header": header, "confidence": round(conf, 2), "source": "ai"}
        return base_mapping
    except Exception:
        return base_mapping


def parse_file(content: bytes, filename: str) -> list:
    """Parse CSV / XLSX / XLS into a list of sheets with headers and rows."""
    lower = filename.lower()
    sheets = []
    if lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        sheets.append(_sheet_payload("Sheet1", df))
    elif lower.endswith((".xlsx", ".xls")):
        xl = pd.ExcelFile(io.BytesIO(content))
        for name in xl.sheet_names:
            df = xl.parse(name, dtype=str, keep_default_na=False)
            if df.empty:
                continue
            sheets.append(_sheet_payload(name, df))
    else:
        raise ValueError("Unsupported file type. Upload a CSV, XLSX or XLS file.")
    if not sheets:
        raise ValueError("No readable sheets with data found in the file.")
    return sheets


def _sheet_payload(name: str, df: pd.DataFrame) -> dict:
    df = df.dropna(how="all")
    rows = df.where(pd.notna(df), None).to_dict(orient="records")
    rows = [{str(k): (None if v in ("", None) else str(v).strip()) for k, v in r.items()} for r in rows]
    truncated = len(rows) > MAX_STORED_ROWS
    return {
        "name": name,
        "headers": [str(c) for c in df.columns],
        "row_count": len(rows),
        "rows": rows[:MAX_STORED_ROWS],
        "truncated": truncated,
    }


def _parse_amount(raw):
    if raw is None:
        return None
    try:
        cleaned = re.sub(r"[^\d.\-]", "", str(raw).replace(",", ""))
        if cleaned in ("", "-", "."):
            return None
        value = float(cleaned)
        return value if value > 0 else None
    except Exception:
        return None


def validate_and_normalize(rows: list, mapping: dict, source: str, batch_id: str | None = None, simulated: bool = False) -> dict:
    """Pure validation + normalization. Rows that fail never enter financial
    calculations; they go to the exception report with explicit reasons."""
    report = {
        "total_rows": len(rows),
        "valid_rows": 0,
        "invalid_rows": 0,
        "duplicate_rows": 0,
        "invalid_amounts": 0,
        "invalid_dates": 0,
        "unsupported_statuses": 0,
        "missing_linkage": 0,
        "generated_payment_ids": 0,
        "estimated_timestamps": 0,
        "row_errors": [],
    }
    records, exceptions = [], []
    seen_payment_ids = set()

    def get(row, field):
        header = mapping.get(field)
        if not header:
            return None
        value = row.get(header)
        return value if value not in ("", None) else None

    for index, row in enumerate(rows):
        errors = []
        payment_id = get(row, "payment_id")
        order_id = get(row, "order_id")
        invoice_id = get(row, "invoice_id")
        amount = _parse_amount(get(row, "amount"))
        status = normalize_status(get(row, "status"))
        raw_ts = get(row, "timestamp")
        parsed_ts = None
        if raw_ts:
            candidate = pd.to_datetime(raw_ts, utc=True, errors="coerce")
            if pd.isna(candidate):
                errors.append("invalid_date")
                report["invalid_dates"] += 1
            else:
                parsed_ts = candidate.isoformat()

        if payment_id and payment_id in seen_payment_ids:
            report["duplicate_rows"] += 1
            if len(report["row_errors"]) < 50:
                report["row_errors"].append({"row": index + 2, "errors": ["duplicate_payment_id"]})
            exceptions.append({
                "exception_id": f"exc_{uuid.uuid4().hex[:12]}",
                "reason": "duplicate_payment_id",
                "record_ref": payment_id,
                "detail": {k: v for k, v in row.items() if v is not None},
                "status": "OPEN",
                "source": source,
                "batch_id": batch_id,
                "created_at": now_iso(),
            })
            continue
        if amount is None:
            errors.append("invalid_amount")
            report["invalid_amounts"] += 1
        if status is None:
            errors.append("unsupported_status")
            report["unsupported_statuses"] += 1
        if not order_id and not invoice_id:
            errors.append("missing_linkage")
            report["missing_linkage"] += 1

        if errors:
            report["invalid_rows"] += 1
            if len(report["row_errors"]) < 50:
                report["row_errors"].append({"row": index + 2, "errors": sorted(set(errors))})
            exceptions.append({
                "exception_id": f"exc_{uuid.uuid4().hex[:12]}",
                "reason": ",".join(sorted(set(errors))),
                "record_ref": payment_id or order_id or invoice_id or f"row {index + 2}",
                "detail": {k: v for k, v in row.items() if v is not None},
                "status": "OPEN",
                "source": source,
                "batch_id": batch_id,
                "created_at": now_iso(),
            })
            continue

        generated_id = False
        if not payment_id:
            payment_id = f"pay_{uuid.uuid4().hex[:12]}"
            generated_id = True
            report["generated_payment_ids"] += 1
        estimated_ts = False
        if not parsed_ts:
            parsed_ts = now_iso()
            estimated_ts = True
            report["estimated_timestamps"] += 1

        seen_payment_ids.add(payment_id)
        report["valid_rows"] += 1
        currency = get(row, "currency")
        records.append({
            "payment_id": payment_id,
            "order_id": order_id,
            "invoice_id": invoice_id,
            "customer_reference": get(row, "customer_reference"),
            "amount": round(amount, 2),
            "currency": currency.upper() if currency else None,
            "status": status,
            "failure_code": get(row, "failure_code"),
            "failure_reason": get(row, "failure_reason"),
            "payment_method": get(row, "payment_method"),
            "timestamp": parsed_ts,
            "source": source,
            "source_event_id": None,
            "simulated": simulated,
            "ingestion_confidence": 1.0 if not (generated_id or estimated_ts) else 0.8,
            "payment_id_generated": generated_id,
            "timestamp_estimated": estimated_ts,
            "raw_data_reference": f"{source}:{batch_id or 'adhoc'}:row{index + 2}",
            "batch_id": batch_id,
            "ingested_at": now_iso(),
        })

    report["rows_to_exception_queue"] = len(exceptions)
    return {"report": report, "records": records, "exceptions": exceptions}
