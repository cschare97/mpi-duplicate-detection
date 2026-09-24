#!/usr/bin/env python3
"""
unmatched_results_report.py

Scans OpenEMR's `pnotes` table for lab-result notifications that were
generated for a newly-created "skeleton" patient — i.e. an incoming HL7
result that could not be auto-matched to an existing patient record via
match_patient() in receive_hl7_results.inc.php.

Detection logic:
    create_skeleton_patient() prepends the literal text
        "New Patient for Pid: <pid> <fname> <lname> <dob> created. "
    onto $orphanLog, which is then prepended to every labNotice() message
    body written to pnotes.body. A routine, successfully-matched result
    does NOT get this prefix — its body starts directly with the
    "<lab>-<order_id> Order ordered by..." text.

    This script filters pnotes.title = 'Lab Results' rows whose body
    matches that skeleton-patient prefix, extracts the embedded patient
    info, and prints a clean review report.

Usage:
    # Against a real OpenEMR database (flags, or a .env file — see below):
    python3 unmatched_results_report.py --host localhost --port 8320 \\
        --user <user> --password <password> --database <database> --days 30

    # No database needed — runs against built-in sample data:
    python3 unmatched_results_report.py --demo

Config via .env (optional):
    Create a .env file alongside this script (see .env.example) with:
        DB_HOST=localhost
        DB_PORT=8320
        DB_USER=<user>
        DB_PASSWORD=<password>
        DB_NAME=<database>
    Any value can still be overridden with its --flag on the command line.

This script only ever SELECTs from pnotes — it never writes to the
database.
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timedelta

# Matches the fixed text create_skeleton_patient() writes into $orphanLog:
#   "New Patient for Pid: 456 John Doe 1980-01-01 created. "
# The name is captured as one field, because first and last name cannot be
# split reliably when either contains spaces (e.g. "Mary Ann", "De La Cruz").
SKELETON_PATTERN = re.compile(
    r"New Patient for Pid:\s*(?P<pid>\d+)\s+"
    r"(?P<name>.+?)\s+"
    r"(?P<dob>\d{4}-\d{2}-\d{2})\s+created\."
)

# Built-in sample data for --demo mode: one skeleton-patient event and one
# routine, successfully-matched result, modeled directly on the real
# pnotes.body format produced by labNotice() in receive_hl7_results.inc.php.
DEMO_ROWS = [
    {
        "id": 1,
        "date": "2026-08-06 14:02:00",
        "body": (
            "New Patient for Pid: 999 Jane Smith 1985-03-12 created. "
            "QuestLab-1029 Order ordered by Dr. Smith with lab result file "
            "creation date on 2026-08-06 13:55 and specimen collections on "
            "2026-08-05 09:10 has been created. Please review these items "
            "to ensure proper resolution of order results."
        ),
        "pid": 999,
        "assigned_to": "admin",
        "message_status": "New",
    },
    {
        "id": 2,
        "date": "2026-08-06 14:10:00",
        "body": (
            "LabCorp-1030 Order ordered by Dr. Jones with lab result file "
            "creation date on 2026-08-06 14:05 and specimen collections on "
            "2026-08-05 10:00 has been created. Please review these items "
            "to ensure proper resolution of order results."
        ),
        "pid": 12,
        "assigned_to": "admin",
        "message_status": "New",
    },
    {
        "id": 3,
        "date": "2026-08-06 14:20:00",
        "body": (
            "New Patient for Pid: 1001 Mary Ann Smith 1972-11-05 created. "
            "QuestLab-1031 Order ordered by Dr. Jones with lab result file "
            "creation date on 2026-08-06 14:15 and specimen collections on "
            "2026-08-05 11:30 has been created. Please review these items "
            "to ensure proper resolution of order results."
        ),
        "pid": 1001,
        "assigned_to": "admin",
        "message_status": "New",
    },
]


def load_dotenv(path=".env"):
    """Minimal .env loader — no external dependency. Silently does nothing
    if the file doesn't exist. Existing environment variables are not
    overwritten (so real env vars always win over the file)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def parse_args():
    p = argparse.ArgumentParser(
        description="Report unmatched (skeleton-patient) lab results from OpenEMR's pnotes table."
    )
    p.add_argument("--demo", action="store_true",
                    help="Run against built-in sample data — no database connection needed.")
    p.add_argument("--host", default=os.environ.get("DB_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("DB_PORT", 3306)))
    p.add_argument("--user", default=os.environ.get("DB_USER"))
    p.add_argument("--password", default=os.environ.get("DB_PASSWORD"))
    p.add_argument("--database", default=os.environ.get("DB_NAME", "openemr"))
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only include notices from the last N days (default: all)",
    )
    p.add_argument(
        "--csv",
        default=None,
        help="Optional path to also write results as CSV",
    )
    args = p.parse_args()

    if not args.demo and (not args.user or not args.password):
        p.error(
            "--user and --password are required unless --demo is set "
            "(set them via flags, or DB_USER / DB_PASSWORD in a .env file)"
        )
    return args


def fetch_lab_result_notes(conn, days=None):
    """Pull all Lab Results pnotes rows, optionally bounded to the last N days."""
    import pymysql.cursors

    query = (
        "SELECT id, date, body, pid, assigned_to, message_status "
        "FROM pnotes "
        "WHERE title = 'Lab Results'"
    )
    params = []
    if days is not None:
        query += " AND date >= %s"
        params.append(datetime.now() - timedelta(days=days))
    query += " ORDER BY date DESC"

    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def extract_skeleton_events(rows):
    """Filter to rows whose body indicates a skeleton patient was created,
    and pull out the structured fields embedded in the free text."""
    events = []
    for row in rows:
        match = SKELETON_PATTERN.search(row["body"] or "")
        if not match:
            continue
        events.append(
            {
                "pnote_id": row["id"],
                "date": row["date"],
                "skeleton_pid": match.group("pid"),
                "name": match.group("name"),
                "dob": match.group("dob"),
                "assigned_to": row["assigned_to"],
                "message_status": row["message_status"],
                "full_body": row["body"],
            }
        )
    return events


def print_report(events, demo=False):
    if not events:
        print("No unmatched-result / skeleton-patient events found.")
        return

    header = "UNMATCHED LAB RESULTS — REQUIRES REVIEW"
    if demo:
        header += "  [DEMO MODE — sample data, not a live database]"

    print(f"\n{'='*70}")
    print(f"{header}  ({len(events)} found)")
    print(f"{'='*70}\n")

    for e in events:
        print(f"[pnote #{e['pnote_id']}] {e['date']}")
        print(f"  New skeleton patient: PID {e['skeleton_pid']} — {e['name']} (DOB {e['dob']})")
        print(f"  Assigned to: {e['assigned_to'] or '(unassigned)'}   Status: {e['message_status']}")
        print(f"  Full note: {e['full_body']}")
        print("-" * 70)

    print(f"\nTotal unmatched results requiring review: {len(events)}\n")


def write_csv(events, path):
    fieldnames = ["pnote_id", "date", "skeleton_pid", "name", "dob", "assigned_to", "message_status"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for e in events:
            writer.writerow(e)
    print(f"Wrote {len(events)} rows to {path}")


def main():
    load_dotenv()
    args = parse_args()

    if args.demo:
        rows = DEMO_ROWS
        events = extract_skeleton_events(rows)
        print_report(events, demo=True)
        if args.csv:
            write_csv(events, args.csv)
        return

    try:
        import pymysql
    except ImportError:
        sys.exit(
            "Missing dependency: pymysql\n"
            "Install it with: pip3 install pymysql --break-system-packages\n"
            "(or: pip3 install pymysql --user)"
        )

    conn = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
    )

    try:
        rows = fetch_lab_result_notes(conn, days=args.days)
        events = extract_skeleton_events(rows)
        print_report(events)
        if args.csv:
            write_csv(events, args.csv)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
