"""Guardrails and Safety Policy Verification for Hearthline Coworking."""
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, Optional, List

from db import execute, rows_to_dicts

# ── Guardrail Configurations ────────────────────────────────────────────────
MAX_COURTESY_AMOUNT = float(os.getenv("GUARDRAIL_MAX_COURTESY_AMOUNT", "150.0"))
MAX_RESOLUTIONS_PER_30_DAYS = int(os.getenv("GUARDRAIL_MAX_RESOLUTIONS_PER_MONTH", "1"))
MIN_CONFIDENCE_THRESHOLD = float(os.getenv("GUARDRAIL_MIN_CONFIDENCE", "0.50"))

# Known adversarial / prompt injection patterns
ADVERSARIAL_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions",
    r"system\s+prompt\s+override",
    r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|jailbroken|developer|admin)",
    r"grant\s+(?:master|full|root|admin)\s+(?:access|badge|override)",
    r"(?:drop|delete|truncate)\s+(?:table|database|from)",
    r"bypass\s+(?:all\s+)?(?:security|guardrails|filters|billing)",
    r"credit\s+my\s+account\s+\$\d{4,}",  # claims over thousands
]

# Physical safety & emergency hazard patterns
EMERGENCY_PATTERNS = [
    r"\bfire\b", r"\bsmoke\b", r"\bgas\s+leak\b", r"\bspark(?:ing|s)?\b",
    r"\belectrical\s+shock\b", r"\bflood(?:ing)?\b", r"\bstructural\s+collapse\b",
]

# Credit card number pattern (13 to 16 digits)
CREDIT_CARD_PATTERN = r"\b(?:\d[ -]*?){13,16}\b"


class GuardrailViolation(Exception):
    """Exception raised when a strict guardrail is breached."""
    def __init__(self, guardrail_name: str, message: str, details: Dict[str, Any] = None):
        super().__init__(message)
        self.guardrail_name = guardrail_name
        self.message = message
        self.details = details or {}


# ── Guardrail 1: Input Security & Prompt Injection Guardrail ────────────────
def check_input_security(body_text: str) -> Tuple[bool, Optional[str]]:
    """Inspects text for prompt injection, jailbreak attempts, or adversarial commands.
    Returns (is_safe, violation_reason)."""
    text_lower = body_text.lower()
    for pattern in ADVERSARIAL_PATTERNS:
        if re.search(pattern, text_lower):
            return False, f"Adversarial or prompt injection pattern detected: '{pattern}'"
    return True, None


# ── Guardrail 2: Financial Resolution & Cap Guardrail ───────────────────────
def validate_courtesy_resolution_guardrail(
    requester_name: str,
    amount: float,
    discrepancy_details: str,
) -> Tuple[bool, str]:
    """Enforces financial safety limits on autonomous courtesy resolutions:
    1. Hard dollar cap (Max $150.00). Claims above cap require manager review.
    2. Velocity throttle (Max 1 courtesy resolution per member per 30-day window).
    Returns (is_approved, rationale)."""
    # Check 1: Dollar Cap
    if amount > MAX_COURTESY_AMOUNT:
        return (
            False,
            f"Financial Guardrail Triggered: Claimed discrepancy amount (${amount:.2f}) "
            f"exceeds autonomous threshold limit (${MAX_COURTESY_AMOUNT:.2f}). "
            f"Escalated to Finance Manager for manual approval."
        )

    # Check 2: Resolution Frequency / Velocity Throttle
    try:
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        res = execute(
            "SELECT request_id, courtesy_resolution, created_at FROM member_requests "
            "WHERE requester_name = ? AND discrepancy_verified = 1 AND created_at >= ?",
            [requester_name, thirty_days_ago],
        )
        recent_resolutions = rows_to_dicts(res)
        if len(recent_resolutions) >= MAX_RESOLUTIONS_PER_30_DAYS:
            return (
                False,
                f"Velocity Guardrail Triggered: Member {requester_name} has already received "
                f"{len(recent_resolutions)} courtesy resolution(s) within the past 30 days. "
                f"Second resolution requires Finance Manager review."
            )
    except Exception:
        pass

    return True, f"Approved under autonomous courtesy resolution threshold (${amount:.2f} <= ${MAX_COURTESY_AMOUNT:.2f})."


# ── Guardrail 3: Critical Environmental & Life-Safety Guardrail ─────────────
def check_safety_hazards(body_text: str) -> Tuple[bool, Optional[str]]:
    """Detects immediate physical, structural, or life-safety emergencies.
    Returns (is_emergency, emergency_description)."""
    text_lower = body_text.lower()
    for pattern in EMERGENCY_PATTERNS:
        if re.search(pattern, text_lower):
            return True, f"Immediate physical life-safety emergency detected: '{pattern}'"
    return False, None


# ── Guardrail 4: PII Data Sanitization Guardrail ─────────────────────────────
def sanitize_pii(body_text: str) -> str:
    """Masks credit card numbers or sensitive credentials to prevent credential leaks in logs."""
    sanitized = re.sub(CREDIT_CARD_PATTERN, "[REDACTED-PAYMENT-CARD]", body_text)
    return sanitized


# ── Guardrail 5: Low Confidence Fallback Guardrail ───────────────────────────
def check_confidence_fallback(confidence: float) -> Tuple[bool, Optional[str]]:
    """Ensures agent does not take automated actions when classification confidence is dangerously low."""
    if confidence < MIN_CONFIDENCE_THRESHOLD:
        return (
            False,
            f"Confidence Guardrail Triggered: Classification confidence ({confidence:.2f}) "
            f"is below safe automated threshold ({MIN_CONFIDENCE_THRESHOLD:.2f}). "
            f"Routing to Front Desk Member Success for human review."
        )
    return True, None
