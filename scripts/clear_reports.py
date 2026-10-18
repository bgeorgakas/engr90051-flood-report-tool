"""Delete all reports from the database and reset report IDs to start at 1.

Uses DATABASE_URL from the environment, or from .env in the project root.

Usage: python scripts/clear_reports.py [--yes]
"""

from pathlib import Path
from urllib.parse import urlsplit
import argparse
import os
import sys

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (environment or .env)")
    host = urlsplit(url).hostname or "local socket"

    with psycopg.connect(url) as conn:
        count = conn.execute("SELECT count(*) FROM reports").fetchone()[0]
        if count == 0:
            print(f"No reports in database on {host}; nothing to delete.")
            return

        if not args.yes:
            answer = input(f"Delete all {count} reports from database on {host}? Type 'yes' to confirm: ")
            if answer.strip().lower() != "yes":
                print("Cancelled.")
                return

        conn.execute("TRUNCATE reports RESTART IDENTITY")
    print(f"Deleted {count} reports from {host}.")


if __name__ == "__main__":
    main()
