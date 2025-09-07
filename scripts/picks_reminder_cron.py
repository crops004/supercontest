"""
Tiny helper that triggers the picks reminder cron endpoint.

Env vars:
  CRON_SECRET  (required) – must match your web service’s CRON_SECRET
  CRON_URL     (optional) – defaults to your production URL
  WEEK         (optional) – override the week (?week=)
  FORCE        (optional) – "1"/"true" to bypass time window (?force=1)
"""

import os
import sys
import urllib.parse
import urllib.request


def main() -> int:
    token = os.environ.get("CRON_SECRET")
    if not token:
        print("CRON_SECRET is not set", file=sys.stderr)
        return 2

    base = os.environ.get(
        "CRON_URL",
        "https://supercontest.onrender.com/admin/internal/cron/picks-reminder",
    )

    params = {}
    if os.environ.get("WEEK"):
        params["week"] = os.environ["WEEK"]
    if os.environ.get("FORCE"):
        params["force"] = os.environ["FORCE"]

    url = base
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, method="POST",
                                 headers={"X-CRON-TOKEN": token})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            print("Status:", resp.status)
            print("Body:", body)
            return 0 if 200 <= resp.status < 300 else 1
    except Exception as e:
        print("Error:", repr(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
