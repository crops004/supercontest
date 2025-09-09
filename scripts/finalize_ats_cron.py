import os
import requests

BASE_URL = os.getenv("APP_BASE_URL", "https://your-app.onrender.com")
TOKEN = os.getenv("CRON_SECRET")

week = os.getenv("WEEK")  # optional override
params = {}
if week:
    params["week"] = week

url = f"{BASE_URL}/admin/internal/cron/finalize-ats"
resp = requests.post(url, headers={"X-CRON-TOKEN": TOKEN}, params=params)

print("Status:", resp.status_code)
print(resp.text)
