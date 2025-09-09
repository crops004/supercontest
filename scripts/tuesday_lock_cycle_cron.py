import os
import requests

def main():
    url = os.getenv("CRON_URL")
    token = os.getenv("CRON_SECRET")

    if not url or not token:
        raise RuntimeError("Missing CRON_URL or CRON_SECRET environment variable")

    resp = requests.post(url, headers={"X-CRON-TOKEN": token})
    print("Tuesday Lock Cycle cron")
    print("Status:", resp.status_code)
    print(resp.text)

if __name__ == "__main__":
    main()
