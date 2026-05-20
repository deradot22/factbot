"""Локальный режим: тянем апдейты через getUpdates вместо webhook."""
import time
import logging
import requests

from app import TG_API, handle_update, log


def delete_webhook():
    try:
        r = requests.post(f"{TG_API}/deleteWebhook", json={"drop_pending_updates": False}, timeout=10)
        log.info("deleteWebhook: %s", r.text)
    except requests.RequestException as e:
        log.warning("deleteWebhook failed: %s", e)


def main():
    delete_webhook()
    offset = 0
    log.info("poll loop started")
    while True:
        try:
            r = requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": '["message","edited_message"]'},
                timeout=40,
            )
            if not r.ok:
                log.warning("getUpdates http %s: %s", r.status_code, r.text)
                time.sleep(3)
                continue
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception:
                    log.exception("handle_update crashed")
        except requests.RequestException as e:
            log.warning("network: %s", e)
            time.sleep(3)
        except KeyboardInterrupt:
            log.info("bye")
            break


if __name__ == "__main__":
    main()
