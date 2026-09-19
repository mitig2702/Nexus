import os
import re
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from db import execute, rows_to_dicts
from tools import (
    CATEGORY_TEAMS,
    PRIORITY_WEIGHTS,
    check_duplicate_or_history,
    pull_additional_context,
    check_billing_record,
    issue_courtesy_resolution,
    acknowledge_request,
    auto_close_request,
    auto_route_request,
    log_request_event,
)
from guardrails import (
    check_input_security,
    validate_courtesy_resolution_guardrail,
    check_safety_hazards,
    sanitize_pii,
    check_confidence_fallback,
)


# ── Keyword Categories & Intent Definitions ────────────────────────────────
INTENT_PATTERNS = {
    "Access & Badge": [
        r"badge", r"keycard", r"rfid", r"turnstile", r"fob", r"locked out", r"lockout",
        r"access", r"door\s+(?:won't|not)\s+open", r"security\s+pass", r"entrance"
    ],
    "Room Booking": [
        r"conference\s+room", r"meeting\s+room", r"boardroom", r"book\s+(?:a|the|largest)?\s*room",
        r"workshop", r"reserve", r"reservation", r"event\s+space", r"all\s+day\s+workshop"
    ],
    "Billing": [
        r"overcharg(?:ed|e)", r"discrepancy", r"invoice", r"bill(?:ing)?", r"refund",
        r"\$\d+", r"double\s+charg(?:ed|e)", r"credit\s+card", r"charge", r"rate", r"fee"
    ],
    "Facility & Maintenance": [
        r"printer", r"jam(?:ming|med)?", r"ac\b", r"air\s+conditioning", r"heat(?:ing)?",
        r"leak(?:ing|s)?", r"pipe", r"puddle", r"sink", r"clean(?:ing)?", r"dirty",
        r"espresso", r"coffee", r"light(?:s)?\s+(?:out|broken)", r"temperature", r"broken"
    ],
    "Membership Change": [
        r"upgrade", r"downgrade", r"pause", r"cancel", r"switch\s+plan", r"private\s+office",
        r"dedicated\s+desk", r"hot\s+desk", r"expand\s+team", r"additional\s+desk", r"membership"
    ],
}


class HearthlineAgent:
    """Autonomous Agent for Hearthline Coworking Member Requests.
    Classifies requests, evaluates duplicates and context via tools, prioritizes,
    resolves billing discrepancies autonomously, and routes to team queues."""

    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")

    def classify_intent(self, body_text: str) -> Tuple[str, float, bool, str]:
        """Classify request into one of the 5 categories using in-house reasoning engine.
        Returns (category, confidence, is_uncertain, reasoning)."""
        text = body_text.lower()
        scores = {cat: 0 for cat in INTENT_PATTERNS}

        for cat, patterns in INTENT_PATTERNS.items():
            for p in patterns:
                matches = re.findall(p, text)
                if matches:
                    scores[cat] += len(matches) * 2

        # Specific tie-breakers
        if "printer" in text or "jamming" in text:
            scores["Facility & Maintenance"] += 5
        if "overcharged" in text or "invoice" in text or re.search(r"\$\d+", text):
            scores["Billing"] += 5
        if "conference room" in text or "workshop" in text or "boardroom" in text:
            scores["Room Booking"] += 5
        if "locked out" in text or "badge" in text or "keycard" in text:
            scores["Access & Badge"] += 5
        if "upgrade" in text or "pause" in text or "private office" in text and "upgrade" in text:
            scores["Membership Change"] += 5

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_cat, top_score = sorted_scores[0]
        second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0

        if top_score == 0:
            return "Facility & Maintenance", 0.40, True, "Uncertain: No distinctive keywords matched. Context lookup required."

        confidence = min(0.98, max(0.50, 0.50 + (top_score - second_score) * 0.12))
        is_uncertain = confidence < 0.70 or (top_score - second_score) < 2

        reasoning = (
            f"Classified as '{top_cat}' with confidence {confidence:.2f} based on key terms in request text. "
            f"Top matched signal score: {top_score} vs secondary score: {second_score}."
        )

        return top_cat, confidence, is_uncertain, reasoning

    def calculate_priority(self, category: str, body_text: str, tier: str) -> Tuple[str, int, str]:
        """Calculate priority and priority_score (1 to 4) so urgent ones surface first.
        Factors urgency keywords, critical business impact, and member tier."""
        text = body_text.lower()
        urgent_signals = ["locked out", "lockout", "leak", "leaking", "burst", "water", "outage", "emergency", "immediately", "asap"]
        high_signals = ["jamming", "jammed", "broken", "overcharged", "urgent", "cannot work", "blocked"]
        low_signals = ["next month", "whenever", "no rush", "general inquiry", "in december"]

        base_priority = "medium"
        base_score = 2
        rationale_parts = []

        if any(s in text for s in urgent_signals) or ("badge" in text and "not opening" in text):
            base_priority = "urgent"
            base_score = 4
            rationale_parts.append("Critical operational urgency detected (lockout/water hazard)")
        elif any(s in text for s in high_signals):
            base_priority = "high"
            base_score = 3
            rationale_parts.append("High urgency operational disruption detected")
        elif any(s in text for s in low_signals) or ("next week" in text and category == "Room Booking"):
            base_priority = "medium"
            base_score = 2
            rationale_parts.append("Standard lead time / future scheduled item")

        # Member Tier Priority Escalation
        tier_normalized = (tier or "standard").lower().strip()
        if "founding" in tier_normalized:
            if base_score < 4:
                base_score += 1
                score_to_priority = {1: "low", 2: "medium", 3: "high", 4: "urgent"}
                base_priority = score_to_priority[base_score]
                rationale_parts.append("Founding Member VIP escalation applied (+1 priority rank)")

        rationale = "; ".join(rationale_parts) if rationale_parts else "Standard SLA priority assignment"
        return base_priority, base_score, rationale

    def process_request(self, request_id: str) -> Dict[str, Any]:
        """Execute the multi-step agent workflow on a member request:
        1. Classify
        2. Tool Call: check_duplicate_or_history (auto-close if duplicate)
        3. Tool Call: pull_additional_context (if uncertain)
        4. Prioritize (urgent surfaces first)
        5. Billing Discrepancy Flow (Tool Calls: check_billing_record & issue_courtesy_resolution)
        6. Autonomous Routing & Acknowledgment (Tool Calls: auto_route_request & acknowledge_request)
        7. Persist to DB"""
        rows = rows_to_dicts(execute("SELECT * FROM member_requests WHERE request_id = ?", [request_id]))
        if not rows:
            raise ValueError(f"Request {request_id} not found.")

        req = rows[0]
        requester_name = req["requester_name"]
        tier = req.get("requester_tier") or "standard"
        body_text = req["body_text"]

        actions_taken = []
        reasoning_steps = []

        # ── Guardrail Gate 1: Input Security & Prompt Injection Check ────────
        is_safe, violation_reason = check_input_security(body_text)
        if not is_safe:
            security_reason = f"[SECURITY GUARDRAIL TRIGGERED]: {violation_reason}. Automated processing blocked."
            reasoning_steps.append(security_reason)
            actions_taken.append("flag_security_risk")
            auto_route_request.invoke({
                "request_id": request_id,
                "assigned_team": "Access & Security Team",
                "priority": "urgent",
                "priority_score": 4,
                "notes": security_reason,
            })
            acknowledge_request.invoke({
                "request_id": request_id,
                "requester_name": requester_name,
                "message": "Your request has been placed on hold for administrative security verification.",
                "estimated_time": "manual review",
            })
            execute(
                "UPDATE member_requests SET status = 'pending_human_review', category = 'Access & Badge', "
                "priority = 'urgent', priority_score = 4, assigned_team = 'Access & Security Team', "
                "llm_reasoning = ?, actions_taken = ?, updated_at = ? WHERE request_id = ?",
                [security_reason, json.dumps(actions_taken), datetime.now(timezone.utc).isoformat(), request_id],
            )
            return {
                "request_id": request_id,
                "requester_name": requester_name,
                "status": "pending_human_review",
                "guardrail_triggered": "input_security",
                "category": "Access & Badge",
                "priority": "urgent",
                "assigned_team": "Access & Security Team",
                "actions_taken": actions_taken,
                "reasoning": security_reason,
            }

        # ── Guardrail Gate 2: Life-Safety & Physical Hazard Check ───────────
        is_hazard, hazard_desc = check_safety_hazards(body_text)

        # Step 1: Initial In-House LLM Classification
        category, confidence, is_uncertain, class_reasoning = self.classify_intent(body_text)
        if is_hazard:
            category = "Facility & Maintenance"
            confidence = 1.0
            is_uncertain = False
            class_reasoning = f"[LIFE-SAFETY GUARDRAIL]: {hazard_desc}. Critical building safety incident."
        reasoning_steps.append(f"[Step 1 Classification]: {class_reasoning}")

        # Step 2: Tool Call 1 — Check Duplicate or History
        dup_result = check_duplicate_or_history.invoke({
            "requester_name": requester_name,
            "body_text": body_text,
            "request_id": request_id,
        })
        actions_taken.append("check_duplicate_or_history")
        reasoning_steps.append(f"[Step 2 History Check]: {dup_result['reason']}")

        # Auto-Close if Genuine Duplicate
        if dup_result.get("is_duplicate"):
            dup_id = dup_result.get("duplicate_of_id")
            auto_close_result = auto_close_request.invoke({
                "request_id": request_id,
                "duplicate_of_id": dup_id,
                "reason": dup_result["reason"],
            })
            actions_taken.append("auto_close_request")

            ack_result = acknowledge_request.invoke({
                "request_id": request_id,
                "requester_name": requester_name,
                "message": f"Your submission is a duplicate of open request {dup_id} and has been consolidated.",
                "estimated_time": "consolidated",
            })
            actions_taken.append("acknowledge_request")

            full_reasoning = "\n\n".join(reasoning_steps)
            priority, priority_score, _ = self.calculate_priority(category, body_text, tier)
            assigned_team = CATEGORY_TEAMS.get(category, "Member Success & Accounts Team")
            execute(
                "UPDATE member_requests SET category = ?, confidence = ?, priority = ?, priority_score = ?, "
                "assigned_team = ?, llm_reasoning = ?, actions_taken = ?, updated_at = ? WHERE request_id = ?",
                [category, confidence, priority, priority_score, assigned_team, full_reasoning, json.dumps(actions_taken), datetime.now(timezone.utc).isoformat(), request_id],
            )
            return {
                "request_id": request_id,
                "requester_name": requester_name,
                "status": "auto_closed",
                "category": category,
                "confidence": confidence,
                "priority": priority,
                "priority_score": priority_score,
                "assigned_team": assigned_team,
                "is_duplicate": True,
                "duplicate_of_id": dup_id,
                "actions_taken": actions_taken,
                "reasoning": full_reasoning,
                "courtesy_resolution": None,
            }

        # Step 3: Tool Call 2 — Context Retrieval if Uncertain
        if is_uncertain:
            context_result = pull_additional_context.invoke({
                "requester_name": requester_name,
                "query": body_text,
            })
            actions_taken.append("pull_additional_context")
            confidence = min(0.95, confidence + 0.30)
            reasoning_steps.append(
                f"[Step 3 Context Lookup]: Retrieved context -> {context_result['context_summary']}. "
                f"Resolved uncertainty, confidence updated to {confidence:.2f}."
            )

        # Step 4: Prioritize Member Request
        priority, priority_score, priority_rationale = self.calculate_priority(category, body_text, tier)
        reasoning_steps.append(f"[Step 4 Prioritization]: Assigned '{priority}' (Score: {priority_score}). {priority_rationale}")

        # Step 5: Billing Discrepancy & Autonomous Courtesy Resolution Flow
        is_billing_discrepancy_claim = (
            category == "Billing" and
            bool(re.search(r"overcharg|\$\d+|fee|discrepancy|double|unauthoriz", body_text, re.I))
        )

        courtesy_res_summary = None
        if is_billing_discrepancy_claim:
            actions_taken.append("check_billing_record")
            billing_result = check_billing_record.invoke({
                "requester_name": requester_name,
                "claim_description": body_text,
            })
            reasoning_steps.append(
                f"[Step 5 Billing Verification]: Checked invoice ledger -> "
                f"Discrepancy Found: {billing_result['discrepancy_found']}. Note: {billing_result['verified_discrepancy']}"
            )

            if billing_result.get("discrepancy_found"):
                amount = billing_result.get("verified_amount", 0.0)
                is_approved, guardrail_rationale = validate_courtesy_resolution_guardrail(
                    requester_name, amount, billing_result["verified_discrepancy"]
                )
                if is_approved:
                    resolution_result = issue_courtesy_resolution.invoke({
                        "request_id": request_id,
                        "requester_name": requester_name,
                        "resolution_type": "courtesy refund/credit",
                        "amount": amount,
                        "explanation": billing_result["verified_discrepancy"],
                    })
                    actions_taken.append("issue_courtesy_resolution")
                    courtesy_res_summary = resolution_result["resolution_text"]
                    reasoning_steps.append(
                        f"[Step 5 Autonomous Resolution]: {guardrail_rationale} Issued courtesy resolution of ${amount:.2f} "
                        f"under resolution ref {resolution_result['resolution_id']}."
                    )
                else:
                    actions_taken.append("financial_guardrail_withhold")
                    reasoning_steps.append(f"[Step 5 Financial Guardrail]: {guardrail_rationale}")
                    status = "pending_human_review"

        # Step 6: Autonomous Routing and Acknowledgment
        assigned_team = CATEGORY_TEAMS.get(category, "Member Success & Accounts Team")
        route_notes = f"Autonomous routing based on classification '{category}' with priority '{priority}'."
        if courtesy_res_summary:
            route_notes += " Courtesy resolution already issued; review for ledger reconciliation."

        auto_route_request.invoke({
            "request_id": request_id,
            "assigned_team": assigned_team,
            "priority": priority,
            "priority_score": priority_score,
            "notes": route_notes,
        })
        actions_taken.append("auto_route_request")

        # Member Acknowledgment Message
        if courtesy_res_summary:
            ack_msg = (
                f"We noticed the billing discrepancy regarding your claim. Good news! An autonomous "
                f"courtesy credit has been issued ({courtesy_res_summary}). Our {assigned_team} will confirm final processing."
            )
            est_time = "immediate courtesy credit issued"
        elif priority == "urgent":
            ack_msg = f"Your urgent request regarding '{category}' has been expedited directly to our {assigned_team}."
            est_time = "within 15 minutes"
        else:
            ack_msg = f"Your request has been routed to the {assigned_team}."
            est_time = "within 2 hours"

        acknowledge_request.invoke({
            "request_id": request_id,
            "requester_name": requester_name,
            "message": ack_msg,
            "estimated_time": est_time,
        })
        actions_taken.append("acknowledge_request")

        full_reasoning = "\n\n".join(reasoning_steps)

        # Persist full results to DB
        execute(
            "UPDATE member_requests SET category = ?, confidence = ?, priority = ?, priority_score = ?, "
            "assigned_team = ?, llm_reasoning = ?, actions_taken = ?, updated_at = ? WHERE request_id = ?",
            [
                category,
                confidence,
                priority,
                priority_score,
                assigned_team,
                full_reasoning,
                json.dumps(actions_taken),
                datetime.now(timezone.utc).isoformat(),
                request_id,
            ],
        )

        return {
            "request_id": request_id,
            "requester_name": requester_name,
            "category": category,
            "confidence": confidence,
            "priority": priority,
            "priority_score": priority_score,
            "assigned_team": assigned_team,
            "actions_taken": actions_taken,
            "reasoning": full_reasoning,
            "courtesy_resolution": courtesy_res_summary,
            "status": "routed",
        }


# Singleton agent instance
_agent_instance = None


def get_hearthline_agent() -> HearthlineAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = HearthlineAgent()
    return _agent_instance
