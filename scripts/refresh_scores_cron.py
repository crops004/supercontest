import os
import sys
import requests

def main():
    url = os.getenv("CRON_URL")  # e.g. https://supercontest.onrender.com/admin/internal/cron/refresh-scores
    token = os.getenv("CRON_SECRET")
    days_from = os.getenv("DAYS_FROM", "3").strip()  # default 3; override per job/time slot

    if not url or not token:
        raise RuntimeError("Missing CRON_URL or CRON_SECRET environment variable")

    try:
        resp = requests.post(
            url,
            params={"days_from": days_from},           # endpoint should accept this
            headers={"X-CRON-TOKEN": token},
            timeout=30,
        )
    except requests.RequestException as e:
        print("Refresh Scores cron: request failed", file=sys.stderr)
        print(repr(e), file=sys.stderr)
        sys.exit(1)

    print("Refresh Scores cron")
    print("Status:", resp.status_code)
    print(resp.text)

if __name__ == "__main__":
    main()
