import os
import json
import random
import logging
from pathlib import Path
from datetime import date, timedelta

import requests
from flask import Flask, request, jsonify, abort
from anthropic import Anthropic

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("factbot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CRON_SECRET = os.environ.get("CRON_SECRET", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
DATA_FILE = Path(os.environ.get("DATA_FILE", "factbot.json"))
DAILY_PICKS = int(os.environ.get("DAILY_PICKS", "3"))
COOLDOWN_DAYS = int(os.environ.get("COOLDOWN_DAYS", "5"))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)


def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("data file corrupted, resetting")
    return {"chats": {}}


def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def tg(method, **payload):
    try:
        r = requests.post(f"{TG_API}/{method}", json=payload, timeout=15)
        if not r.ok:
            log.warning("TG %s failed [%s]: %s", method, r.status_code, r.text)
        return r.json() if r.ok else None
    except requests.RequestException as e:
        log.warning("TG %s exception: %s", method, e)
        return None


def send(chat_id, text):
    return tg("sendMessage", chat_id=chat_id, text=text)


SYSTEM_PROMPT = (
    "Ты — генератор смешных «фактов дня» про людей из дружеского чата. "
    "Стиль: дерзкий, нагло-туалетный, физиологический юмор — пуки, какашки, "
    "пиписьки, отрыжки, бредовые телесные ситуации. "
    "ВАЖНЫЕ ГРАНИЦЫ: не трогай внешность, вес, расу, нацию, религию, "
    "ориентацию, родителей, реальных третьих лиц. Никакого насилия. "
    "Только абсурдная бредовая физиология и комичные бытовые позоры. "
    "Формат: одно-два коротких предложения, начинай с указания времени "
    "(«Утром», «Сегодня», «Ночью», «На рассвете»). "
    "Без эмодзи, без хэштегов, без вступлений, без морали. Только сам факт. "
    "Язык: русский."
)


def generate_fact(name):
    msg = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Имя: {name}. Придумай один свежий дерзкий факт дня. Не повторяй банальностей."
        }],
    )
    return msg.content[0].text.strip()


def pick_participants(chat_state, n):
    pool = [(uid, p) for uid, p in chat_state["participants"].items() if not p.get("skip")]
    if not pool:
        return []
    cutoff = (date.today() - timedelta(days=COOLDOWN_DAYS)).isoformat()
    recent = {h["user_id"] for h in chat_state.get("history", []) if h["date"] >= cutoff}
    fresh = [x for x in pool if x[0] not in recent]
    if len(fresh) < n:
        fresh = pool
    return random.sample(fresh, min(n, len(fresh)))


def run_for_chat(chat_id):
    data = load_data()
    chat_state = data["chats"].get(str(chat_id))
    if not chat_state:
        send(chat_id, "В пуле никого. Напишите что-нибудь — попадёте в факт-рулетку.")
        return
    picks = pick_participants(chat_state, DAILY_PICKS)
    if not picks:
        send(chat_id, "В пуле никого. Напишите что-нибудь — попадёте в факт-рулетку.")
        return

    lines = []
    today = date.today().isoformat()
    for uid, p in picks:
        try:
            fact = generate_fact(p["name"])
            lines.append(fact)
            chat_state.setdefault("history", []).append({"date": today, "user_id": uid})
        except Exception:
            log.exception("generate_fact failed for %s", p.get("name"))

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    chat_state["history"] = [h for h in chat_state.get("history", []) if h["date"] >= cutoff]
    save_data(data)

    if lines:
        send(chat_id, "Доброе утро. Сводка из жизни группы:\n\n" + "\n\n".join(f"— {ln}" for ln in lines))


def handle_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat = msg.get("chat", {})
    if chat.get("type") not in ("group", "supergroup"):
        return
    user = msg.get("from") or {}
    if user.get("is_bot"):
        return

    chat_id = chat["id"]
    user_id = str(user["id"])
    name = user.get("first_name") or user.get("username") or "Аноним"
    text = (msg.get("text") or "").strip()

    data = load_data()
    chat_state = data["chats"].setdefault(str(chat_id), {"participants": {}, "history": []})

    cmd = text.split()[0].split("@")[0].lower() if text else ""

    if cmd == "/start":
        send(chat_id, (
            "Привет. Я каждое утро (08:00 по Таиланду) присылаю несколько дерзких фактов про вас.\n\n"
            "Напишите что-нибудь — попадёте в пул. Команды:\n"
            "/list — кто сейчас в пуле\n"
            "/skip — выйти из рулетки\n"
            "/back — вернуться\n"
            "/fact — сгенерить факты прямо сейчас"
        ))
        return

    if cmd == "/list":
        names = [p["name"] for p in chat_state["participants"].values() if not p.get("skip")]
        send(chat_id, "В пуле: " + (", ".join(names) if names else "никого"))
        return

    if cmd == "/skip":
        chat_state["participants"].setdefault(user_id, {"name": name, "skip": False})
        chat_state["participants"][user_id]["skip"] = True
        save_data(data)
        send(chat_id, f"Окей, {name}, ты выпал из факт-рулетки. /back чтобы вернуться.")
        return

    if cmd == "/back":
        if user_id in chat_state["participants"]:
            chat_state["participants"][user_id]["skip"] = False
            save_data(data)
            send(chat_id, f"{name} возвращается в пул.")
        else:
            send(chat_id, "Тебя ещё не было в пуле — напиши что-нибудь.")
        return

    if cmd == "/fact":
        chat_state["participants"].setdefault(user_id, {"name": name, "skip": False})
        chat_state["participants"][user_id]["name"] = name
        save_data(data)
        run_for_chat(chat_id)
        return

    p = chat_state["participants"].setdefault(user_id, {"name": name, "skip": False})
    p["name"] = name
    save_data(data)


@app.post("/webhook/<secret>")
def webhook(secret):
    if not WEBHOOK_SECRET or secret != WEBHOOK_SECRET:
        abort(403)
    update = request.get_json(force=True, silent=True) or {}
    try:
        handle_update(update)
    except Exception:
        log.exception("handle_update failed")
    return "ok"


@app.post("/send-daily")
def send_daily():
    if not CRON_SECRET or request.args.get("key") != CRON_SECRET:
        abort(403)
    data = load_data()
    chats = list(data["chats"].keys())
    log.info("daily run for %d chats", len(chats))
    for chat_id in chats:
        try:
            run_for_chat(int(chat_id))
        except Exception:
            log.exception("daily failed for %s", chat_id)
    return jsonify(ok=True, chats=len(chats))


@app.get("/health")
def health():
    return "ok"


@app.get("/")
def index():
    return "factbot is alive"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
