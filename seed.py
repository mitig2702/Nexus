"""Seed database for Hearthline Coworking Member Requests & Invoices."""
import os
import csv
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from db import execute, migrate, rows_to_dicts

SEED_MEMBERS = [
    ("Elena Rostova", "founding member", "Hearthline Downtown", "Dedicated Desk #D-14", "HL-BADGE-1002", "Desk D-14"),
    ("Marcus Chen", "standard", "Hearthline Midtown", "Hot Desk Unlimited", "HL-BADGE-2041", "2nd Floor Open"),
    ("Sarah Jenkins", "founding member", "Hearthline Downtown", "Private Office 6-person (#PO-301)", "HL-BADGE-1009", "Office 301"),
    ("David Kim", "standard", "Hearthline Downtown", "Dedicated Desk #D-08", "HL-BADGE-1022", "Desk D-08"),
    ("Aisha Patel", "founding member", "Hearthline Downtown", "Private Office 4-person (#PO-402)", "HL-BADGE-1014", "Office 402"),
    ("Lucas Moreau", "standard", "Hearthline Midtown", "2x Dedicated Desks", "HL-BADGE-2055", "Desks M-11/12"),
    ("Sophia Martinez", "standard", "Hearthline Downtown", "Hot Desk", "HL-BADGE-1077", "1st Floor Hot Desks"),
    ("James Wilson", "founding member", "Hearthline Downtown", "Private Office 10-person (#PO-502)", "HL-BADGE-1033", "Office 502"),
    ("Rachel Green", "standard", "Hearthline Midtown", "Hot Desk Unlimited", "HL-BADGE-2088", "3rd Floor Hot Desks"),
    ("Tom Bradley", "standard", "Hearthline Midtown", "Dedicated Desk", "HL-BADGE-2099", "Desk M-04"),
    ("Chloe Davis", "founding member", "Hearthline Downtown", "Private Office 8-person (#PO-501)", "HL-BADGE-1045", "Office 501"),
]


def seed_database(base_dir: str = None) -> dict:
    if base_dir is None:
        base_dir = os.path.dirname(__file__)

    print("Running database migrations...")
    migrate()

    # 1. Seed Members
    print("Seeding Hearthline members...")
    for m in SEED_MEMBERS:
        execute(
            "INSERT OR REPLACE INTO members (requester_name, requester_tier, location, plan_type, active_badge_id, assigned_space) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            list(m),
        )

    # 2. Seed Invoices
    invoice_csv_path = os.path.join(base_dir, "invoice.csv")
    invoice_count = 0
    if os.path.exists(invoice_csv_path):
        print(f"Seeding invoices from {invoice_csv_path}...")
        execute("DELETE FROM invoices")
        with open(invoice_csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=1):
                inv_num = f"INV-2026-0{i}"
                disc = row.get("verified_discrepancy", "None")
                amt = 0.0
                if "$34" in disc:
                    amt = 34.0
                elif "$50" in disc:
                    amt = 50.0
                elif "$120" in disc:
                    amt = 120.0
                execute(
                    "INSERT INTO invoices (requester_name, invoice_number, amount, verified_discrepancy, billing_date, status) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [row["requester_name"], inv_num, amt, disc, "2026-09-01", "paid"],
                )
                invoice_count += 1

    # 3. Seed Member Requests
    requests_csv_path = os.path.join(base_dir, "member_requests.csv")
    request_count = 0
    if os.path.exists(requests_csv_path):
        print(f"Seeding member requests from {requests_csv_path}...")
        execute("DELETE FROM member_requests")
        with open(requests_csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            now = datetime.now(timezone.utc).isoformat()
            for row in reader:
                execute(
                    "INSERT OR REPLACE INTO member_requests "
                    "(request_id, requester_name, requester_tier, status, submitted_at, body_text, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        row["request_id"],
                        row["requester_name"],
                        row.get("requester_tier", "standard"),
                        row.get("status", "new"),
                        row["submitted_at"],
                        row["body_text"],
                        now,
                        now,
                    ],
                )
                request_count += 1

    print(f"Seeding complete: {len(SEED_MEMBERS)} members, {invoice_count} invoices, {request_count} requests.")
    return {
        "members": len(SEED_MEMBERS),
        "invoices": invoice_count,
        "requests": request_count,
        "status": "seeded",
    }


if __name__ == "__main__":
    res = seed_database()
    print("Seed result:", res)
