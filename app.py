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
    load_dotenv(override=True)
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("factbot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
DATA_FILE = Path(os.environ.get("DATA_FILE", "factbot.json"))
DAILY_PICKS = int(os.environ.get("DAILY_PICKS", "3"))
COOLDOWN_DAYS = int(os.environ.get("COOLDOWN_DAYS", "5"))

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
log.info("startup: anthropic=%s, data_file=%s", "configured" if anthropic else "MISSING", DATA_FILE)


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
    "Ты — генератор кринжовых «фактов дня» про людей из дружеского чата. "
    "Цель: каждый факт ДОЛЖЕН быть из новой темы, не однотипный. "
    "Тебе будет дан конкретный жанр на этот раз — следуй ему. "
    "Если жанра нет — изобрети неожиданный.\n\n"

    "ОБЩИЕ ПРИНЦИПЫ КРИНЖА:\n"
    "— конкретика: место, свидетель, объект, звук/запах/субстанция/предмет/реплика;\n"
    "— неожиданный поворот в конце (панч), а не банальное «и все засмеялись»;\n"
    "— разговорный, чуть быдловатый русский: «жопа», «бзднул», «обосрался», "
    "«бабка», «мужик», «братан» — нормально; откровенный мат (хуй/пизда/блядь) — нет.\n\n"

    "ГРАНИЦЫ (строго):\n"
    "— НЕ трогать: внешность/вес/рост, расу, нацию, религию, ориентацию, "
    "родителей героя, реальных третьих лиц по имени, политических деятелей;\n"
    "— нет насилия, крови, секса, наркотиков, инвалидности, болезней, смерти;\n"
    "— нельзя унижать по уязвимым признакам — только бредовые ситуации.\n\n"

    "ФОРМАТ — СТРОГО:\n"
    "— ровно ОДНО предложение, 12-25 слов;\n"
    "— начни с обстоятельства времени/места (Утром / Сегодня в обед / Ночью / "
    "Вчера в маршрутке / В очереди в Пятёрочке / На совещании / В лифте / "
    "На остановке / В душе / Перед сном);\n"
    "— ОБЯЗАТЕЛЬНО конкретика и неожиданный поворот;\n"
    "— без эмодзи, без хэштегов, без вступлений, без 'факт:', без многоточий;\n"
    "— НЕ повторяй темы из примеров буквально — придумывай свежее.\n\n"

    "АНТИ-ПАТТЕРН (на этот мы ВЫГОРЕЛИ — НЕ делай больше так):\n"
    "— «X громко пукнул, и кто-то что-то заметил». БАН на пуки/бздежи как основной "
    "сюжет, если жанр не «физиология». Используй пуки максимум как фоновую деталь, "
    "не как центр факта.\n\n"

    "ПРИМЕРЫ ХОРОШИХ (разные жанры, разные сюжеты):\n"
    "— На совещании Алина случайно назвала директора «мамой» и три минуты притворялась, что это был чих.\n"
    "— Сегодня Серёжа купил в Пятёрочке йогурт, открыл — а там его собственное обручальное кольцо, которое он терял полгода.\n"
    "— Вчера в маршрутке Дима задремал и проснулся на коленях у мужика, который вёл с ним длинный разговор про карбюратор.\n"
    "— Утром Катя залила в кофемашину куриный бульон, выпила и пошла на работу с уверенностью, что у бариста кризис.\n"
    "— В лифте Михаил поздоровался с зеркалом восемь раз, потом обиделся, что оно не отвечает.\n"
    "— На йоге Алина так резко выдохнула, что инструктор подумал — она просветлилась, и попросил автограф.\n"
    "— Ночью Серёжа во сне продал свою машину коту, и кот теперь требует документы.\n"
    "— В душе Дима пытался помыть голову туалетной уткой, и обвинил в этом курс рубля.\n\n"

    "ПРИМЕРЫ С ВЗАИМОДЕЙСТВИЕМ (если в каст-листе есть другие имена):\n"
    "— Сегодня в кафе Катя случайно отхлебнула из чашки Михаила, и теперь они официально женаты по законам пятилетнего ребёнка за соседним столом.\n"
    "— Вчера Дима написал Алине «спокноки» вместо «спокойной ночи», и она два часа гуглила, что это за угроза.\n"
    "— Утром Серёжа подарил Михаилу носок из своей пары, чтобы хоть кто-то носил парный комплект.\n\n"

    "Язык: русский, живой, разговорный."
)


# Жанры. На каждый факт выбирается случайный — гарантирует разнообразие.
GENRES = [
    "странный сон, который перетёк в реальность",
    "конфуз с едой (перепутал, пролил, нашёл странное внутри)",
    "разговор с неодушевлённым предметом (банкомат, дверь, чайник, лифт)",
    "случайная встреча с соседом/курьером/кассиром/охранником",
    "конфуз в общественном транспорте (но НЕ про пуки)",
    "странное открытие про себя (нашёл, обнаружил, забыл)",
    "провал бытового плана (хотел одно — получил другое)",
    "кринж на работе / на учёбе (ответ не туда, оговорка)",
    "случай с домашним питомцем (питомец сделал что-то странное)",
    "перепутал инструкции / ингредиенты / адреса",
    "техно-фейл (отправил не тому, заблокировал не то, нажал не туда)",
    "странная реакция на стресс (засмеялся, расплакался, заговорил по-эстонски)",
    "спортивный или физкультурный позор (упал, застрял, забыл движение)",
    "магазинный кринж (в очереди, на кассе, в примерочной)",
    "ночной мини-кошмар (заблудился в собственной квартире, проснулся в шкафу)",
    "общение с соседями (что-то перелетело через балкон)",
    "встреча с детьми/бабкой/мужиком, который что-то непонятное сказал",
    "взаимодействие с гаджетом, который повёл себя не так",
    "физиология (пуки/отрыжки/живот) — ТОЛЬКО если этот жанр выпал, иначе мимо",
]


def generate_fact(name, others=None, avoid=None):
    if anthropic is None:
        return f"[нет ANTHROPIC_API_KEY в .env — фейковый факт] Утром {name} проснулся и обнаружил, что превратился в пельмень."
    others = others or []
    avoid = avoid or []
    genre = random.choice(GENRES)
    log.info("generate_fact name=%s genre=%r others=%s avoid=%d", name, genre, others, len(avoid))

    if others:
        others_str = ", ".join(others)
        cast_line = (
            f"Главный герой: {name}. "
            f"В чате также есть: {others_str}. "
            "С вероятностью ~50% вставь в факт ОДНОГО из них как "
            "свидетеля / жертву / соучастника / случайного прохожего — "
            "по имени, как оно дано. Остальных не упоминай."
        )
    else:
        cast_line = f"Главный герой: {name}."

    avoid_line = ""
    if avoid:
        avoid_block = "\n".join(f"— {a}" for a in avoid[:5])
        avoid_line = (
            "\n\nНЕ повторяй сюжеты и стилистику из недавних фактов "
            "(другая тема, другая ситуация, другой панч):\n" + avoid_block
        )

    msg = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=140,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"{cast_line}\n\n"
                f"ЖАНР НА ЭТОТ РАЗ (обязательно): {genre}.\n\n"
                "Один кринжовый факт дня. Одно предложение, 12-25 слов. "
                "Время/место → конкретика → неожиданный поворот. "
                "Не пук как центр сюжета (если жанр не «физиология»). "
                "Никаких объяснений и морали."
                + avoid_line
            )
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

    all_active = [pp["name"] for uid2, pp in chat_state["participants"].items() if not pp.get("skip")]

    history = chat_state.setdefault("history", [])
    recent_facts = [h.get("fact") for h in history[-8:] if h.get("fact")]

    lines = []
    today = date.today().isoformat()
    for uid, p in picks:
        others_pool = [n for n in all_active if n != p["name"]]
        others = random.sample(others_pool, min(3, len(others_pool)))
        try:
            avoid = (recent_facts + lines)[-5:]
            fact = generate_fact(p["name"], others=others, avoid=avoid)
            lines.append(fact)
            history.append({"date": today, "user_id": uid, "fact": fact})
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
