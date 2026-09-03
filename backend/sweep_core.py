"""Verification sweep core (Phase 2B) — shared by the manual API route and the
platform cron. Single implementation; no parallel systems.

Idempotent and safe to run repeatedly: it only re-verifies open cases against
source-of-truth payment data and reconciles provider cases against the genuine
Razorpay API. It never claims recovery — only verifies state. Every run is
persisted (verification_sweeps) and audited.
"""
import asyncio

from audit import write_audit
from constants import OPEN_CASE_STATUSES, now_iso
from database import db
from detection import verify_case
from integrations_store import get_integration
from providers.base import IntegrationError
from providers.razorpay_adapter import RazorpayAdapter


async def run_verification_sweep(actor: str) -> dict:
    await write_audit(actor=actor, event_type="RECONCILIATION_STARTED", reason="Verification sweep started.")
    open_cases = await db.recovery_cases.find({"status": {"$in": OPEN_CASE_STATUSES}}, {"_id": 0}).to_list(2000)
    results = {"checked": 0, "verified_recovered": 0, "closed_natural": 0, "not_recovered": 0, "pending": 0, "provider_reconciled": 0, "provider_errors": 0, "already_closed": 0}

    config = await get_integration("razorpay", "TEST")
    adapter = RazorpayAdapter(config) if config and config.get("status") == "CONNECTED" else None

    for case in open_cases:
        results["checked"] += 1
        # Only TEST-sourced cases are reconciled with TEST credentials —
        # LIVE cases are never touched with TEST credentials (isolation).
        # Synthetic orders (test-lab/simulator) 404 forever at the provider:
        # they are stamped MISSING_PROVIDER_DATA once and skipped afterwards so
        # the reconciliation-failure signal stays meaningful across sweeps.
        if adapter and case.get("source") == "RAZORPAY_TEST" and (case.get("reconciliation") or {}).get("status") != "MISSING_PROVIDER_DATA":
            try:
                remote = await asyncio.to_thread(adapter.fetch_order, case["order_key"])
                if remote.get("status") == "paid":
                    from detection import process_normalized_order_event
                    await process_normalized_order_event({
                        "kind": "order", "provider": "razorpay", "provider_event_id": None,
                        "event_type": "order.paid", "order_id": case["order_key"],
                        "amount": round((remote.get("amount") or 0) / 100, 2),
                        "amount_paid": round((remote.get("amount_paid") or 0) / 100, 2),
                        "amount_due": round((remote.get("amount_due") or 0) / 100, 2),
                        "currency": remote.get("currency"), "status": "paid",
                        "receipt": remote.get("receipt"), "created_at": None,
                        "source": case.get("source"), "source_mode": (config or {}).get("mode", "TEST"),
                    }, actor=f"reconciliation:{actor}")
                    results["provider_reconciled"] += 1
                await db.recovery_cases.update_one(
                    {"case_id": case["case_id"]},
                    {"$set": {"reconciliation": {"status": "MATCHED", "checked_at": now_iso(), "method": "provider_api_fetch",
                                                 "observed_order_status": remote.get("status")}}},
                )
            except IntegrationError as exc:
                results["provider_errors"] += 1
                await db.recovery_cases.update_one(
                    {"case_id": case["case_id"]},
                    {"$set": {"reconciliation": {"status": "MISSING_PROVIDER_DATA", "checked_at": now_iso(), "method": "provider_api_fetch"}}},
                )
                await write_audit(case_id=case["case_id"], actor=actor, event_type="RECONCILIATION_FAILED",
                                  reason=f"Provider reconciliation failed: {exc}")
        outcome = await verify_case(case["case_id"], actor=f"sweep:{actor}")
        key = outcome.get("result", "pending")
        results[key] = results.get(key, 0) + 1

    await write_audit(actor=actor, event_type="RECONCILIATION_COMPLETED", reason=f"Verification sweep completed: {results['checked']} cases checked.", after_state=results)
    await db.verification_sweeps.insert_one({"run_at": now_iso(), "actor": actor, "results": results})
    return results
