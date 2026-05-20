import os
import json
import random
import logging
from pathlib import Path
from datetime import date, timedelta, datetime, timezone

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
MSG_TTL_HOURS = int(os.environ.get("MSG_TTL_HOURS", "36"))
MSG_MAX = int(os.environ.get("MSG_MAX", "100"))
MSG_TEXT_MAX = int(os.environ.get("MSG_TEXT_MAX", "200"))

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
    "Ты — генератор кринж-фактов про конкретных людей из дружеской компании "
    "(поселок, кемпинг, баня, падел, качалка, караоке, магазин у дома).\n\n"

    "ФОРМАТ — КЛЮЧЕВОЕ И СТРОГОЕ:\n"
    "— ровно ОДНО предложение, 8-18 слов, не больше;\n"
    "— это сухая констатация ситуации, БЕЗ панча в конце, БЕЗ «и теперь его зовут», "
    "БЕЗ «после чего», БЕЗ объяснения, БЕЗ морали, БЕЗ свидетелей-комментаторов;\n"
    "— стоп ровно там, где ситуация задана. Меньше драмы — больше сухого абсурда;\n"
    "— начинай с обстоятельства места/времени (На кемпинге / В бане / На паделе / "
    "В палатке / На тренировке / После пятой / Утром / В магазине / На репетиции / "
    "На рыбалке / В машине);\n"
    "— без эмодзи, без хэштегов, без 'факт:', без многоточий и тире-вставок.\n\n"

    "ТЕМЫ (целиться сюда, на каждый факт — один сюжетный ход):\n"
    "— туалет и физиология (пёрнул, обоссал, обкакался, отрыгнул, потёк);\n"
    "— мемная неловкая близость между пацанами или девками "
    "(случайно прильнул, залез в спальник, толкнулся жопой, перепутал палатку, "
    "поцеловались по пьяни) — это НЕ про ориентацию, это про «упс, неловко»;\n"
    "— бытовой кринж на природе/в поселке (бабка, сосед, магазин, машина).\n\n"

    "ГРАНИЦЫ (строго):\n"
    "— НЕ оскорблять по реальным признакам: расе, нации, религии, ориентации "
    "как ярлык, весу, инвалидности, болезни;\n"
    "— НЕ использовать слова «гей», «лесби», «педик», «гомик» — описывать ТОЛЬКО позу/ситуацию;\n"
    "— нет реального насилия, крови, секса, наркотиков, политики, родителей героя.\n\n"

    "ПЕРСОНАЖИ ЧАТА И ИХ ФИШКИ "
    "(используй фишку, если герой/участник попался в каст-листе):\n"
    "— Серёга / Сергей — ненавидит помидоры, реагирует на помидор бешенством;\n"
    "— Паша / Павел — на любую ситуацию говорит коротко «Все»;\n"
    "— Катя — фанатка качалки, везде приседает и жмёт;\n"
    "— Алина — тусовщица, караоке, рисует, поёт, движуха;\n"
    "— Миша / Михаил — трудоголик, машины, падел.\n\n"

    "ПРИМЕРЫ В ПРАВИЛЬНОМ ФОРМАТЕ "
    "(заметь: после ситуации СРАЗУ точка, никакого продолжения):\n"
    "— Миша на паделе так сжал ракетку от концентрации, что пёрднул в момент подачи.\n"
    "— На кемпинге Серёга обоссал палатку Миши изнутри во сне, утром объяснил это «росой с минералами».\n"
    "— На тренировке Катя так выложилась на приседе, что задница издала восьмисекундный пердёж.\n"
    "— В бане Серёга случайно толкнулся жопой Мише, оба замерли на секунду.\n"
    "— После пятой Алина и Катя начали дуэт «I Will Always Love You», закончили поцелуем в губы под аплодисменты Серёги.\n"
    "— Катя пошла в магазин за курицей, увидела штангу в окне спортзала, забыла про курицу.\n"
    "— В палатке Катя в темноте перепутала спальник и легла к Алине.\n"
    "— На рыбалке Серёга залез в три ночи в спальник к Мише, спросил «ты не против?».\n\n"

    "ПЛОХО (так НЕ писать):\n"
    "— «...и теперь его зовут Тот Самый Мужик»;\n"
    "— «...а Паша мудро резюмировал...»;\n"
    "— «...тренер записал в журнал...»;\n"
    "— «...соседский кот ушёл в монастырь»;\n"
    "— длинные предложения 20+ слов с двумя глаголами действия подряд;\n"
    "— любые объяснения «почему» и «после чего».\n\n"

    "Язык: русский разговорный, можно «жопа/бзднул/обкакался/обоссал»; "
    "без хуй/пизда/блядь."
)


# Сцены из быта этих ребят — на каждый факт случайная.
SCENES = [
    "кемпинг / палатка / костёр",
    "баня / парилка / предбанник",
    "падел-корт",
    "качалка / тренировка / приседы",
    "караоке / репетиция / микрофон",
    "рыбалка / лодка",
    "магазин в поселке / Пятёрочка / касса",
    "кафе / шашлык / летняя веранда",
    "машина / парковка / заправка",
    "утро в спальнике / в обнимку с соседом по палатке",
    "душ / туалет на природе",
    "встреча с бабкой / соседом / собакой во дворе",
    "кухня / готовка / завтрак",
    "ночь в палатке после пятой",
    "вечеринка / танцпол / диван у кого-то дома",
]


# Известные участники чата и их характеры. Если имя в каст-листе совпадает —
# подсунем lore в промпт.
CHAT_LORE = [
    {"keys": ["сергей", "серёга", "серега", "серёжа", "сережа", "sergey", "serega", "serezha", "serge"],
     "trait": "ненавидит помидоры, реагирует на помидор яростью"},
    {"keys": ["павел", "паша", "pavel", "pasha"],
     "trait": "всегда говорит коротко «Все» на любую ситуацию"},
    {"keys": ["катя", "екатерина", "katya", "kate", "katia", "katerina"],
     "trait": "одержима качалкой, везде ищет повод присесть или пожать"},
    {"keys": ["алина", "alina"],
     "trait": "тусовщица, поёт в караоке, рисует, любит музыку"},
    {"keys": ["миша", "михаил", "misha", "mikhail", "mihail"],
     "trait": "трудоголик, фанат машин и падела"},
]


def lookup_trait(name):
    low = (name or "").lower()
    for entry in CHAT_LORE:
        if any(k in low or low in k for k in entry["keys"]):
            return entry["trait"]
    return None


def store_message(chat_state, user_id, name, text):
    msgs = chat_state.setdefault("messages", [])
    msgs.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": str(user_id),
        "name": name,
        "text": text[:MSG_TEXT_MAX],
    })
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=MSG_TTL_HOURS)).isoformat()
    chat_state["messages"] = [m for m in msgs if m.get("ts", "") >= cutoff][-MSG_MAX:]


def generate_fact(name, others=None, avoid=None):
    if anthropic is None:
        return f"[нет ANTHROPIC_API_KEY в .env — фейковый факт] Утром {name} проснулся пельменем."
    others = others or []
    avoid = avoid or []
    scene = random.choice(SCENES)

    hero_trait = lookup_trait(name)
    others_with_traits = [(o, lookup_trait(o)) for o in others]

    log.info(
        "generate_fact name=%s scene=%r hero_trait=%s others=%s avoid=%d",
        name, scene, bool(hero_trait), others, len(avoid),
    )

    lore_lines = []
    if hero_trait:
        lore_lines.append(f"{name}: {hero_trait}")
    for o, t in others_with_traits:
        if t:
            lore_lines.append(f"{o}: {t}")
    lore_block = ""
    if lore_lines:
        lore_block = "\n\nЧерты участников (используй, если уместно):\n" + "\n".join(f"— {l}" for l in lore_lines)

    if others:
        others_str = ", ".join(others)
        cast_line = (
            f"Главный герой: {name}. В компании также есть: {others_str}. "
            "С вероятностью ~50% вставь в факт ОДНОГО из них (по имени). "
            "Остальных не упоминай."
        )
    else:
        cast_line = f"Главный герой: {name}."

    avoid_line = ""
    if avoid:
        avoid_block = "\n".join(f"— {a}" for a in avoid[:5])
        avoid_line = (
            "\n\nНЕ повторяй сюжеты и сцены из недавних фактов:\n" + avoid_block
        )

    msg = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=90,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"{cast_line}\n\n"
                f"СЦЕНА (обязательно): {scene}.\n"
                "Одно предложение, 8-18 слов, констатация ситуации, "
                "БЕЗ панча и БЕЗ продолжения. Стоп там, где ситуация задана."
                + lore_block
                + avoid_line
            )
        }],
    )
    return msg.content[0].text.strip()


CTX_SYSTEM_PROMPT = SYSTEM_PROMPT + (
    "\n\n=== ОСОБЫЙ РЕЖИМ: ФАКТ ПО МОТИВАМ РЕАЛЬНОГО ЧАТА ===\n"
    "Тебе сейчас дадут реальные сообщения из чата за последние сутки. "
    "Твоя задача: найти ЛЮБОЙ цепляющий момент (оговорку, спор, обсуждение похода, "
    "странную тему, мем, обсёрку, фейл, чей-то план) и сделать ОДИН кринж-факт "
    "по мотивам этого. Используй имена авторов реальных сообщений. "
    "Можно слегка преувеличить или выдумать продолжение в духе.\n\n"
    "ЕСЛИ в сообщениях ничего стоящего нет (просто «ок», «привет», «понял», "
    "пара слов без сюжета) — выведи РОВНО строку:\n"
    "SKIP\n"
    "и больше ничего. Не выдавливай факт из ничего.\n\n"
    "Формат факта (если делаешь) — тот же: одно предложение 8-18 слов, "
    "сухая констатация, БЕЗ панча и БЕЗ продолжения, без цитат из чата."
)


def generate_contextual_fact(messages_list):
    """Возвращает строку факта или None если нет темы."""
    if anthropic is None or not messages_list:
        return None
    chat_text = "\n".join(f"{m['name']}: {m['text']}" for m in messages_list)
    log.info("contextual: %d messages, %d chars", len(messages_list), len(chat_text))
    try:
        msg = anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=CTX_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    "Сообщения из чата за последние сутки:\n\n"
                    f"{chat_text}\n\n"
                    "Сделай один факт по мотивам или выведи SKIP."
                )
            }],
        )
        text = msg.content[0].text.strip()
    except Exception:
        log.exception("contextual fact API error")
        return None
    if text.upper().startswith("SKIP"):
        log.info("contextual: SKIP")
        return None
    return text


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

    # Контекстный факт по мотивам реального чата (если есть о чём)
    msgs = chat_state.get("messages", [])[-30:]
    unique_authors = {m.get("user_id") for m in msgs}
    if len(msgs) >= 5 and len(unique_authors) >= 2:
        ctx_fact = generate_contextual_fact(msgs)
        if ctx_fact:
            lines.append(ctx_fact)
            history.append({"date": today, "user_id": "ctx", "fact": ctx_fact})
            picks = picks[:-1] if len(picks) > 1 else picks  # один обычный заменяем контекстным

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
    if text:
        store_message(chat_state, user_id, name, text)
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
