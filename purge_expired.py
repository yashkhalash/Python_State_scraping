"""
CLI entrypoint for the retention/purge job (src/purge.py). Run periodically
(e.g. a daily cron/scheduled task) once DATABASE_URL is configured.

    uv run python purge_expired.py
"""
from dotenv import load_dotenv

load_dotenv()  # loads .env (DATABASE_URL) so this works without manually exporting it first.

from src.db import get_connection
from src.purge import purge_expired_artifacts


def main():
    conn = get_connection()
    try:
        result = purge_expired_artifacts(conn)
        print(f"Purged {result['purged']} expired raw artifact(s).")
        if result["errors"]:
            print(f"{len(result['errors'])} error(s):")
            for err in result["errors"]:
                print(f"  - {err}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
