import os
import sys
import time
import requests

BASE = os.environ["WEBHOOK_BASE_URL"].rstrip("/")
SECRET = os.environ["CRON_SECRET"]


def main():
    url = f"{BASE}/send-daily"
    for attempt in range(4):
        try:
            r = requests.post(url, params={"key": SECRET}, timeout=120)
            print(f"attempt={attempt} status={r.status_code} body={r.text[:200]}")
            if r.ok:
                return 0
        except requests.RequestException as e:
            print(f"attempt={attempt} error={e}")
        time.sleep(15)
    return 1


if __name__ == "__main__":
    sys.exit(main())
