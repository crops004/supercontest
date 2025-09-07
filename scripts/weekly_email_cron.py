# scripts/weekly_email_cron.py
import os, sys, urllib.request, urllib.parse, json

CRON_URL = os.environ.get("CRON_URL")  # e.g. https://yourapp.onrender.com/admin/internal/cron/weekly-email
CRON_SECRET = os.environ.get("CRON_SECRET")

if not CRON_URL or not CRON_SECRET:
    print("Missing CRON_URL or CRON_SECRET", file=sys.stderr)
    sys.exit(2)

# Optional one-off testing: support CRON_FORCE / CRON_WEEK env vars
params = {}
if os.environ.get("CRON_FORCE"): params["force"] = os.environ["CRON_FORCE"]
if os.environ.get("CRON_WEEK"):  params["week"]  = os.environ["CRON_WEEK"]

url = CRON_URL + ("?" + urllib.parse.urlencode(params) if params else "")
req = urllib.request.Request(url, method="POST", headers={"X-CRON-TOKEN": CRON_SECRET})

try:
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", errors="replace")
        print("Status:", r.status)
        print("Body:", body)
        # exit nonzero if the app reported failure
        try:
            j = json.loads(body)
            if not j.get("ok", False):
                sys.exit(1)
        except Exception:
            pass
except Exception as e:
    print("Request failed:", repr(e), file=sys.stderr)
    sys.exit(1)
