"""Phase 3 migration: safely backfill data_stage, is_lab, and funnel_stage on
historical recovery_cases and payment_attempts without modifying financial amounts,
predictions, or outcomes.
"""
import asyncio
import os
import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database import db


async def migrate_stages():
    print("Starting Phase 3 stage migration...")
    cases_cursor = db.recovery_cases.find({})
    migrated_cases = 0
    async for case in cases_cursor:
        order_key = str(case.get("order_key") or "")
        source = case.get("source")
        simulated = bool(case.get("simulated"))
        provider_mode = case.get("provider_mode")
        
        # Determine is_lab
        is_lab = bool(
            case.get("is_lab")
            or case.get("data_stage") == "LAB"
            or order_key.startswith("order_LAB")
            or source == "TEST_LAB"
        )
        
        # Determine data_stage
        if is_lab:
            data_stage = "LAB"
        elif simulated or source in ("SIMULATOR", "TEST"):
            data_stage = "SIMULATED"
        elif source in ("CSV_UPLOAD", "XLSX_UPLOAD", "FILE_IMPORT"):
            data_stage = "IMPORTED"
        elif source == "RAZORPAY_LIVE" or provider_mode == "LIVE":
            data_stage = "LIVE"
        elif source == "RAZORPAY_TEST" or provider_mode == "TEST":
            data_stage = "TEST"
        else:
            data_stage = "TEST"

        # Determine funnel_stage
        status = case.get("status")
        if status == "VERIFIED_RECOVERED":
            funnel_stage = "recovered"
        elif status in ("VERIFYING", "ACTION_EXECUTED"):
            funnel_stage = "executed"
        elif status in ("APPROVAL_PENDING", "ACTION_SCHEDULED"):
            funnel_stage = "ready"
        elif status == "EVALUATED":
            funnel_stage = "evaluated"
        else:
            funnel_stage = "detected"

        update_fields = {}
        if case.get("data_stage") != data_stage:
            update_fields["data_stage"] = data_stage
        if case.get("is_lab") != is_lab:
            update_fields["is_lab"] = is_lab
        if case.get("funnel_stage") != funnel_stage:
            update_fields["funnel_stage"] = funnel_stage

        if update_fields:
            await db.recovery_cases.update_one({"_id": case["_id"]}, {"$set": update_fields})
            migrated_cases += 1

    print(f"Migration completed. Backfilled {migrated_cases} recovery cases.")


if __name__ == "__main__":
    asyncio.run(migrate_stages())
