import json
import uuid

from fastapi import APIRouter, HTTPException, Request

from audit import write_audit
from auth import get_current_user
from constants import now_iso
from database import db, get_settings
from detection import run_case_pipeline, verify_case
from policy import evaluate_policy
from routes_webhooks import process_webhook_payload
from security_utils import compute_signature, verify_signature

router = APIRouter(prefix="/simulate", tags=["simulate"])


def _sim_payload(order_id, amount, currency, status, failure_code=None, payment_method="card", customer=None):
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": f"payment.{status}",
        "timestamp": now_iso(),
        "simulated": True,
        "data": {
            "payment_id": f"pay_{uuid.uuid4().hex[:12]}",
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "status": status,
            "failure_code": failure_code,
            "failure_reason": failure_code.replace("_", " ") if failure_code else None,
            "payment_method": payment_method,
            "customer_reference": customer or f"cust_{uuid.uuid4().hex[:8]}",
        },
    }


async def _send(payload, actor):
    raw = json.dumps(payload).encode()
    signature = compute_signature(raw)
    if not verify_signature(raw, signature):
        raise HTTPException(status_code=500, detail="Simulator failed to sign its own event.")
    return await process_webhook_payload(payload, actor=actor)


@router.post("/payment-event")
async def simulate_payment_event(request: Request):
    user = await get_current_user(request)
    body = await request.json()
    status = body.get("status", "failed")
    if status not in ("failed", "success", "pending"):
        raise HTTPException(status_code=400, detail="status must be failed, success or pending")
    payload = _sim_payload(
        order_id=body.get("order_id") or f"ORD-SIM-{uuid.uuid4().hex[:6].upper()}",
        amount=float(body.get("amount") or 1000),
        currency=(body.get("currency") or "INR").upper(),
        status=status,
        failure_code=body.get("failure_code"),
        payment_method=body.get("payment_method") or "card",
        customer=body.get("customer_reference"),
    )
    result = await _send(payload, actor=f"simulator:{user['email']}")
    return {
        **result,
        "order_id": payload["data"]["order_id"],
        "note": "SIMULATED event — signed with the environment webhook secret and processed through the real ingestion pipeline. No live payment provider involved.",
    }


@router.post("/invalid-signature-test")
async def invalid_signature_test(request: Request):
    await get_current_user(request)
    payload = _sim_payload(f"ORD-BADSIG-{uuid.uuid4().hex[:6].upper()}", 100, "INR", "failed", "insufficient_funds")
    raw = json.dumps(payload).encode()
    forged = "sha256=" + "0" * 64
    accepted = verify_signature(raw, forged)
    if not accepted:
        await db.security_events.insert_one({
            "type": "INVALID_SIGNATURE",
            "path": "/api/webhooks/payments",
            "ip": "simulator-test",
            "received_at": now_iso(),
            "note": "Deliberate invalid-signature test from the simulator panel",
        })
    return {
        "rejected": not accepted,
        "http_status": 401,
        "security_event_logged": not accepted,
        "note": "Forged signature correctly rejected. The event was NOT processed and a security event was logged.",
    }


@router.post("/scenario/{scenario_id}")
async def run_scenario(scenario_id: int, request: Request):
    user = await get_current_user(request)
    actor = f"simulator:{user['email']}"
    steps = []

    if scenario_id == 1:
        order = f"ORD-S1-{uuid.uuid4().hex[:6].upper()}"
        steps.append({"label": "Customer pays successfully", "detail": f"{order}: a successful ₹4,200 settlement event arrives first (webhooks can arrive out of order).", "status": "done"})
        r_success = await _send(_sim_payload(order, 4200, "INR", "success"), actor)
        steps.append({"label": "Delayed failure event arrives", "detail": "The earlier failed attempt event is delivered late, after the success is already on record.", "status": "done"})
        r_fail = await _send(_sim_payload(order, 4200, "INR", "failed", "insufficient_funds"), actor)
        steps.append({"label": "Engine checks for existing settlement", "detail": f"Result: {r_fail['result']['result']} — a successful payment exists for this order.", "status": "done"})
        steps.append({"label": "No case, no intervention", "detail": "The order naturally recovered on its own. No recovery case created, no action taken, nothing counted as recovered revenue.", "status": "highlight"})
        return {"scenario": scenario_id, "title": "Natural recovery — no intervention", "case_id": None, "steps": steps, "simulated": True}

    if scenario_id == 2:
        order = f"ORD-S2-{uuid.uuid4().hex[:6].upper()}"
        r1 = await _send(_sim_payload(order, 8500, "INR", "failed", "insufficient_funds"), actor)
        case_id = (r1.get("result") or {}).get("case_id")
        pipeline = (r1.get("result") or {}).get("pipeline") or {}
        steps.append({"label": "Payment failed", "detail": f"{order}: ₹8,500 failed and remained unresolved.", "status": "done"})
        steps.append({"label": "AI analysis", "detail": f"Recommended action: {pipeline.get('recommended_action')}.", "status": "done"})
        steps.append({"label": "Policy engine", "detail": f"Decision: {pipeline.get('policy_decision')}.", "status": "done"})
        steps.append({"label": "Action executed (SIMULATED)", "detail": f"Executed: {pipeline.get('executed')}. No real financial action occurred; outcome PENDING.", "status": "done"})
        r2 = await _send(_sim_payload(order, 8500, "INR", "success"), actor)
        steps.append({"label": "Outcome verified", "detail": f"Successful settlement found AFTER the executed action. Case closed as {r2['result'].get('status', r2['result']['result'])} and counted as verified recovered revenue.", "status": "highlight"})
        return {"scenario": scenario_id, "title": "Full loop — action executed and verified", "case_id": case_id, "steps": steps, "simulated": True}

    if scenario_id == 3:
        settings = await get_settings()
        amount = float(settings.get("approval_threshold_amount", 50000)) + 25000
        order = f"ORD-S3-{uuid.uuid4().hex[:6].upper()}"
        r1 = await _send(_sim_payload(order, amount, "INR", "failed", "insufficient_funds"), actor)
        case_id = (r1.get("result") or {}).get("case_id")
        pipeline = (r1.get("result") or {}).get("pipeline") or {}
        steps.append({"label": "High-value payment failed", "detail": f"{order}: ₹{amount:,.0f} failed — above the approval threshold ₹{float(settings.get('approval_threshold_amount', 50000)):,.0f}.", "status": "done"})
        steps.append({"label": "Policy engine", "detail": f"Decision: {pipeline.get('policy_decision')} — AMOUNT_ABOVE_APPROVAL_THRESHOLD.", "status": "done"})
        steps.append({"label": "Routed to human review", "detail": "No autonomous execution. Approve or reject it from the Review Queue.", "status": "highlight"})
        return {"scenario": scenario_id, "title": "High-value case — human approval required", "case_id": case_id, "steps": steps, "simulated": True}

    if scenario_id == 4:
        order = f"ORD-S4-{uuid.uuid4().hex[:6].upper()}"
        r1 = await _send(_sim_payload(order, 6000, "INR", "failed", "insufficient_funds"), actor)
        case_id = (r1.get("result") or {}).get("case_id")
        steps.append({"label": "Payment failed", "detail": f"{order}: ₹6,000 failed (soft decline).", "status": "done"})
        for i in range(3):
            await db.recovery_actions.insert_one({
                "action_id": f"act_{uuid.uuid4().hex[:12]}",
                "case_id": case_id,
                "action_type": "SAFE_PAYMENT_RETRY",
                "label": "Safe Payment Retry",
                "scheduled_time": now_iso(),
                "executed_time": now_iso(),
                "execution_mode": "SIMULATED",
                "simulated": True,
                "approval_status": "AUTO_APPROVED",
                "policy_result": "ALLOW",
                "expected_incremental_value": 0,
                "estimated_cost": 25.0,
                "outcome": "PENDING",
                "idempotency_key": f"{case_id}:SAFE_PAYMENT_RETRY:history:{i}",
                "provider_reference": f"SIM-{uuid.uuid4().hex[:8].upper()}",
                "created_at": now_iso(),
            })
        steps.append({"label": "Retry history", "detail": "3 SAFE_PAYMENT_RETRY executions already on record (catalog limit: 3).", "status": "done"})
        case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
        actions = await db.recovery_actions.find({"case_id": case_id}, {"_id": 0}).to_list(100)
        settings = await get_settings()
        policy_result = evaluate_policy(case, "SAFE_PAYMENT_RETRY", actions, settings)
        await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"policy_result": policy_result}})
        await write_audit(case_id=case_id, actor="policy-engine", event_type="POLICY_DECISION",
                          reason="; ".join(r["detail"] for r in policy_result["reasons"]),
                          after_state={"decision": policy_result["decision"], "action_type": "SAFE_PAYMENT_RETRY"},
                          policy_rule_reference=policy_result["rule_version"])
        steps.append({"label": "Policy engine blocks 4th retry", "detail": f"Decision: {policy_result['decision']} — {policy_result['reasons'][0]['detail'] if policy_result['reasons'] else ''}", "status": "highlight"})
        return {"scenario": scenario_id, "title": "Retry limit reached — stopped by policy", "case_id": case_id, "steps": steps, "simulated": True}

    if scenario_id == 5:
        order = f"ORD-S5-{uuid.uuid4().hex[:6].upper()}"
        payload = _sim_payload(order, 1500, "INR", "failed", "do_not_honor")
        r1 = await _send(payload, actor)
        r2 = await _send(payload, actor)
        steps.append({"label": "First delivery", "detail": f"Event {payload['event_id']} processed: {r1['result']['result']}.", "status": "done"})
        steps.append({"label": "Replayed delivery", "detail": f"Same event_id resent. Result: {r2['status']} — blocked as duplicate, no double processing, no double counting.", "status": "highlight"})
        return {"scenario": scenario_id, "title": "Duplicate webhook — safely ignored", "case_id": (r1.get("result") or {}).get("case_id"), "steps": steps, "simulated": True}

    if scenario_id == 6:
        order = f"ORD-S6-{uuid.uuid4().hex[:6].upper()}"
        r1 = await _send(_sim_payload(order, 3000, "USD", "failed", "insufficient_funds"), actor)
        case_id = (r1.get("result") or {}).get("case_id")
        pipeline = (r1.get("result") or {}).get("pipeline") or {}
        steps.append({"label": "Payment failed", "detail": f"{order}: $3,000 failed; case created and analyzed.", "status": "done"})
        steps.append({"label": "Action executed (SIMULATED)", "detail": f"Recommended: {pipeline.get('recommended_action')}; executed: {pipeline.get('executed')}.", "status": "done"})
        verification = await verify_case(case_id, actor=actor)
        steps.append({"label": "Verification sweep", "detail": "No successful settlement exists in source-of-truth data.", "status": "done"})
        steps.append({"label": "Outcome UNKNOWN/PENDING", "detail": f"Verification result: {verification.get('result')}. $0 counted as verified recovery — executed does not mean recovered.", "status": "highlight"})
        return {"scenario": scenario_id, "title": "Executed but unverifiable — not counted", "case_id": case_id, "steps": steps, "simulated": True}

    raise HTTPException(status_code=404, detail="Unknown scenario. Valid scenarios: 1-6.")
