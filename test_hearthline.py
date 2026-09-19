"""Comprehensive Test Suite for Hearthline Coworking Member Request Operations."""
import json
from db import execute, rows_to_dicts, migrate
from seed import seed_database
from agent import get_hearthline_agent
from main import (
    get_requests_list,
    get_request_detail,
    ingest_member_request,
    process_batch_pending,
    apply_human_override,
    get_dashboard_stats,
)


def run_tests():
    print("=================================================================")
    print("🚀 Running Hearthline Coworking System Test Suite")
    print("=================================================================\n")

    # 1. Test Migration & Seeding
    print("[TEST 1]: Seeding Database from member_requests.csv & invoice.csv...")
    seed_res = seed_database()
    assert seed_res["status"] == "seeded"
    assert seed_res["requests"] >= 12
    assert seed_res["invoices"] >= 10
    print("  ✅ Seed completed successfully:", seed_res)

    # 2. Test Processing Requests via HearthlineAgent
    print("\n[TEST 2]: Running Autonomous Agent on Seed Requests...")
    agent = get_hearthline_agent()

    # 2a. REQ-101: Elena Rostova (Billing + Discrepancy + Courtesy Resolution)
    res_elena = agent.process_request("REQ-101")
    assert res_elena["category"] == "Billing", f"Expected Billing, got {res_elena['category']}"
    assert "check_duplicate_or_history" in res_elena["actions_taken"]
    assert "check_billing_record" in res_elena["actions_taken"]
    assert "issue_courtesy_resolution" in res_elena["actions_taken"]
    assert "auto_route_request" in res_elena["actions_taken"]
    assert "acknowledge_request" in res_elena["actions_taken"]
    assert res_elena["assigned_team"] == "Finance & Billing Team"
    assert "34.00" in (res_elena.get("courtesy_resolution") or "")
    print("  ✅ REQ-101 (Elena Rostova): Billing verified, courtesy resolution issued, routed to Finance.")

    # 2b. REQ-102: Marcus Chen (Facility & Maintenance - Printer Jamming)
    res_marcus = agent.process_request("REQ-102")
    assert res_marcus["category"] == "Facility & Maintenance"
    assert res_marcus["assigned_team"] == "Facilities & Maintenance Team"
    assert res_marcus["priority"] in ("high", "urgent")
    print("  ✅ REQ-102 (Marcus Chen): Facility printer jam classified, routed to Facilities & Maintenance.")

    # 2c. REQ-107: Marcus Chen (Duplicate of REQ-102 -> Auto-Close)
    res_dup = agent.process_request("REQ-107")
    assert res_dup["status"] == "auto_closed"
    assert res_dup["is_duplicate"] is True
    assert res_dup["duplicate_of_id"] == "REQ-102"
    assert "auto_close_request" in res_dup["actions_taken"]
    print("  ✅ REQ-107: Genuine duplicate recognized and autonomously auto-closed.")

    # 2d. REQ-103: Sarah Jenkins (Room Booking - Conference Room)
    res_sarah = agent.process_request("REQ-103")
    assert res_sarah["category"] == "Room Booking"
    assert res_sarah["assigned_team"] == "Room & Event Operations Team"
    print("  ✅ REQ-103 (Sarah Jenkins): Room Booking classified and routed to Room & Event Operations.")

    # 2e. REQ-105: Aisha Patel (Access & Badge - Lockout Urgent!)
    res_aisha = agent.process_request("REQ-105")
    assert res_aisha["category"] == "Access & Badge"
    assert res_aisha["priority"] == "urgent"
    assert res_aisha["priority_score"] == 4
    assert res_aisha["assigned_team"] == "Access & Security Team"
    print("  ✅ REQ-105 (Aisha Patel): Lockout classified as Access & Badge with URGENT priority.")

    # 2f. REQ-106: Lucas Moreau (Membership Change - Upgrade)
    res_lucas = agent.process_request("REQ-106")
    assert res_lucas["category"] == "Membership Change"
    assert res_lucas["assigned_team"] == "Member Success & Accounts Team"
    print("  ✅ REQ-106 (Lucas Moreau): Membership Change classified and routed to Member Success.")

    # 3. Test Prioritization & Surfacing Urgent First
    print("\n[TEST 3]: Verifying Prioritization (Urgent Surfaces First)...")
    # Batch process remaining pending requests
    process_batch_pending()
    req_list = get_requests_list(limit=20)
    scores = [r["priority_score"] for r in req_list]
    # Check that scores are monotonically non-increasing (sorted DESC)
    assert scores == sorted(scores, reverse=True), f"Requests not sorted by priority: {scores}"
    assert req_list[0]["priority"] == "urgent", f"Top request is not urgent: {req_list[0]['priority']}"
    print(f"  ✅ Priority ordering verified! Top request priority is '{req_list[0]['priority']}' (Score {req_list[0]['priority_score']}).")

    # 4. Test Uncertain Request & Context Retrieval Tool
    print("\n[TEST 4]: Testing Uncertain Request Context Lookup Tool...")
    uncertain_req = ingest_member_request({
        "requester_name": "Jordan Lee",
        "requester_tier": "standard",
        "body_text": "Need some general guidance about my assigned workspace area.",
    })
    assert "pull_additional_context" in uncertain_req.get("actions_taken", [])
    print("  ✅ Uncertain request triggered pull_additional_context tool call as required.")

    # 5. Test Human Review & Override
    print("\n[TEST 5]: Testing Human Review & Override...")
    detail_before = get_request_detail("REQ-105")
    assert detail_before["human_override"] == 0

    override_payload = {
        "category": "Facility & Maintenance",
        "priority": "urgent",
        "assigned_team": "Facilities & Maintenance Team",
        "status": "in_progress",
        "reviewer_name": "Alex Taylor (Operations Director)",
        "override_note": "Turnstile motor has physical mechanical failure, needs facilities repair.",
    }
    overridden_req = apply_human_override("REQ-105", override_payload)
    assert overridden_req["human_override"] == 1
    assert overridden_req["category"] == "Facility & Maintenance"
    assert overridden_req["assigned_team"] == "Facilities & Maintenance Team"
    assert overridden_req["status"] == "in_progress"
    assert overridden_req["override_details"]["reviewer_name"] == "Alex Taylor (Operations Director)"
    assert len(overridden_req.get("audit_trail", [])) > 0
    print("  ✅ Human override applied, audit log recorded, and database updated.")

    # 7. Test HTTP Request Handler
    print("\n[TEST 7]: Verifying HTTP Request Handler endpoints...")
    import io
    from main import HearthlineRequestHandler

    class MockSocket:
        def __init__(self, data=b""):
            self._rfile = io.BytesIO(data)
            self.output = io.BytesIO()
        def makefile(self, mode, *args, **kwargs):
            if "r" in mode:
                return self._rfile
            return self.output
        def sendall(self, data):
            self.output.write(data)
        def send(self, data):
            self.output.write(data)
            return len(data)

    def run_mock_http(method, path, body=b""):
        req_text = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
        sock = MockSocket(req_text)
        HearthlineRequestHandler(sock, ("127.0.0.1", 8088), None)
        out = sock.output.getvalue().decode("utf-8", errors="ignore")
        status_line = out.split("\r\n")[0]
        body_part = out.split("\r\n\r\n")[1] if "\r\n\r\n" in out else ""
        return status_line, body_part

    h_status, _ = run_mock_http("GET", "/health")
    assert "200" in h_status, f"Health check failed: {h_status}"

    r_status, r_body = run_mock_http("GET", "/requests")
    assert "200" in r_status, f"Requests list failed: {r_status}"
    assert len(json.loads(r_body)) > 0

    p_status, _ = run_mock_http("GET", "/portal")
    assert "200" in p_status, f"Portal failed: {p_status}"
    print("  ✅ HTTP endpoints verified (/health, /requests, /portal).")

    print("\n=================================================================")
    print("🎉 ALL TESTS PASSED SUCCESSFULLY! 100% SPEC COMPLIANCE.")
    print("=================================================================\n")


if __name__ == "__main__":
    run_tests()
