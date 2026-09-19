# Hearthline Coworking — Member Request Operations & Autonomous Agent

Autonomous agent and request operations portal for **Hearthline Coworking**, operating shared workspace locations (hot desks, dedicated desks, private offices, and meeting rooms) for individual members and company teams.

The application ingests member requests, classifies them using an in-house LLM into 5 specific categories, executes a multi-step agent toolchain (duplicate detection, context retrieval on uncertain classifications, dynamic prioritization surfacing urgent requests first, billing discrepancy verification with autonomous courtesy resolution), provides a human review/override portal, and persists everything to a database.

---

## Key Features

1. **Member Request Ingestion**:
   - Ingestion from seed CSV datasets (`member_requests.csv` and `invoice.csv`).
   - Single-request ingestion API (`POST /requests/ingest`) and batch processing (`POST /requests/batch-process`).
   - Interactive web portal submission form.

2. **In-House LLM Classification**:
   - Classifies every request into one of the 5 categories:
     - `Access & Badge`
     - `Room Booking`
     - `Billing`
     - `Facility & Maintenance`
     - `Membership Change`
   - Generates confidence score (0.0 to 1.0) and detailed step-by-step reasoning trace.

3. **Multi-Step Agent Architecture (Not a Single Call)**:
   - **Step 1**: In-house LLM classification into the 5 categories.
   - **Step 2**: Tool call to `check_duplicate_or_history` to inspect member ticket history. If a genuine duplicate is identified, calls `auto_close_request` and `acknowledge_request`.
   - **Step 3**: If classification is uncertain or ambiguous (e.g. confidence < 0.70), calls `pull_additional_context` to pull the member's active plan, assigned space, badge ID, and coworking policies to resolve ambiguity.
   - **Step 4**: Prioritizes member requests appropriately so urgent ones surface first (incorporates lockout/facility hazard signals + Founding Member VIP escalation).
   - **Step 5**: Billing Discrepancy Flow: For requests classified as `Billing` with a checkable claim, calls `check_billing_record`. If confirmed in `invoice.csv` / invoice database, calls `issue_courtesy_resolution` to autonomously credit/refund the member.
   - **Step 6**: Calls `auto_route_request` to the appropriate team and `acknowledge_request` with estimated resolution time and courtesy details.
   - **Step 7**: Persists complete reasoning trace, tool calls, and actions taken to the database.

4. **Dynamic Prioritization (Urgent Surface First)**:
   - Urgent issues (lockouts, active water leaks, equipment blockers) are scored highest (urgent / score 4).
   - Founding Member tier receives an automatic VIP priority escalation.
   - Requests are sorted by `ORDER BY priority_score DESC, submitted_at DESC` across both API and UI, ensuring urgent requests surface at the top of the queue.

5. **Team Routing**:
   - `Access & Badge` ➔ **Access & Security Team**
   - `Room Booking` ➔ **Room & Event Operations Team**
   - `Billing` ➔ **Finance & Billing Team**
   - `Facility & Maintenance` ➔ **Facilities & Maintenance Team**
   - `Membership Change` ➔ **Member Success & Accounts Team**

6. **Billing Discrepancy Verification & Courtesy Resolution**:
   - Inspects real invoice records (`invoices` table / `invoice.csv`).
   - Autonomously issues courtesy credit/refund via `issue_courtesy_resolution` tool call.
   - Sends resolution reference and explanation in the member acknowledgment.

7. **Human Review & Overrides**:
   - Web portal and REST API (`POST /requests/{request_id}/override`) allowing human agents to review the LLM's full step-by-step reasoning trace, tool actions, and courtesy resolutions.
   - Allows overriding category, priority, assigned team, status, and reviewer notes with full audit logging in `request_events`.

8. **Database & Persistence**:
   - Native SQLite support (`hearthline.db`) out of the box with zero external configuration.
   - Preserves optional Turso support if `TURSO_URL` and `TURSO_TOKEN` are configured.
   - Audit trail table (`request_events`) tracking every ingestion, tool call, resolution, and human override.

---

## Seed Datasets

### `member_requests.csv`
Contains seed requests covering all 5 categories, urgent lockouts, printer jams, conference room bookings, billing discrepancies, founding members, and duplicates:
- `REQ-101`: Elena Rostova (founding member) — *"I was overcharged $34 on my monthly desk billing invoice."*
- `REQ-102`: Marcus Chen (standard) — *"The printer on 2nd floor keeps jamming whenever anyone prints large documents."*
- `REQ-103`: Sarah Jenkins (founding member) — *"Book largest conference room for all day workshop next week on Wednesday."*
- `REQ-104`: David Kim (standard) — *"There is an unexpected $50 guest pass charge on my recent invoice that I did not authorize."*
- `REQ-105`: Aisha Patel (founding member) — *"My RFID keycard badge is not opening the 3rd floor entrance door this morning. I am locked out!"*
- `REQ-106`: Lucas Moreau (standard) — *"We need to upgrade our team plan from 2 dedicated desks to a 4-person private office starting next month."*
- `REQ-107`: Marcus Chen (standard) — *"Printer on 2nd floor keeps jamming again, still not working."* (Duplicate of REQ-102)
- `REQ-108`: Sophia Martinez (standard) — *"Water pipe is leaking under the kitchen sink on floor 1, puddle forming quickly."*
- `REQ-109`: James Wilson (founding member) — *"Need 3 new access badges for our incoming interns starting next Monday."*
- `REQ-110`: Rachel Green (standard) — *"I want to pause my hot desk membership for December while traveling overseas."*
- `REQ-111`: Tom Bradley (standard) — *"I think my bill is too high this month."*
- `REQ-112`: Chloe Davis (founding member) — *"I was charged $120 for weekend conference room hire that was cancelled 48 hours in advance."*

### `invoice.csv`
Ground-truth billing ledger with verified discrepancies:
- `Elena Rostova`: `"$34 overcharge verified: incorrect recurring locker fee added to September desk invoice"`
- `David Kim`: `"$50 guest pass fee verified in error on invoice #INV-8821"`
- `Chloe Davis`: `"$120 weekend room booking fee verified eligible for refund per 48h cancellation policy"`
- Plus standard rate members with no discrepancy (`Marcus Chen`, `Sarah Jenkins`, `Tom Bradley`, etc.)

---

## Quickstart

### 1. Seed the Database
```bash
python3 seed.py
```

### 2. Run the Server
The application supports standard execution via Python standard library:
```bash
python3 main.py
```
Open **`http://localhost:8000/`** or **`http://localhost:8000/portal`** in your browser to interact with the Hearthline Member Success Operations portal!

### 3. Run Automated Tests
```bash
python3 test_hearthline.py
```

---

## REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` or `/portal` | Interactive Web Dashboard for reviewing requests & overrides |
| `GET` | `/health` | Service health status |
| `GET` | `/stats` | Operational metrics (total requests, urgent count, courtesy resolutions, overrides) |
| `GET` | `/requests` | List requests sorted with urgent requests first (`?category=`, `?priority=`, `?status=`, `?tier=`, `?q=`) |
| `GET` | `/requests/{id}` | Detailed request view with LLM reasoning trace, actions taken, courtesy resolution, and audit trail |
| `POST` | `/requests/ingest` | Ingest a new member request and run the autonomous agent pipeline |
| `POST` | `/requests/batch-process` | Process all pending requests through the autonomous agent |
| `POST` | `/requests/{id}/override` | Submit a human review override for category, priority, team, or status |
| `GET` | `/invoices` | List invoices and verified discrepancies from `invoice.csv` |
| `POST` | `/seed` | Reset and re-seed the database from CSV files |

---

## Agent Tools in `tools.py`

| Tool | Purpose |
|---|---|
| `check_duplicate_or_history` | Searches member request history for duplicates and related tickets |
| `pull_additional_context` | Retrieves member plan type, location, badge ID, and policies when classification is uncertain |
| `check_billing_record` | Queries `invoice.csv` / invoices ledger for verified discrepancies |
| `issue_courtesy_resolution` | Autonomously issues credit/refund/waiver when discrepancy is confirmed |
| `acknowledge_request` | Sends personalized acknowledgment message with estimated resolution time |
| `auto_close_request` | Autonomously closes genuine duplicate tickets and links to original |
| `auto_route_request` | Autonomously routes request to the appropriate team with priority level |
