import os, re, json, time, html, requests
import xml.etree.ElementTree as ET

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
NITTER = [x.strip() for x in os.environ.get(
    "NITTER_INSTANCES",
    "https://nitter.net,https://nitter.privacyredirect.com,https://nitter.poast.org"
).split(",") if x.strip()]

KEYWORDS = ["وظيفة", "وظائف", "توظيف", "نعلن", "التقديم", "شاغر", "شواغر",
            "فرصة عمل", "فرص وظيفية", "يعلن", "تعلن", "hiring", "vacancy",
            "vacancies", "career", "apply now", "we are hiring", "job"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = "state.json"


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    s.setdefault("accounts", {})
    s.setdefault("offset", 0)
    s.setdefault("alerted", False)
    return s


def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def tg(method, **params):
    try:
        return requests.post(f"{API}/{method}", data=params, timeout=30).json()
    except Exception as e:
        print("tg error", e)
        return {}


def send(chat, text):
    tg("sendMessage", chat_id=chat, text=text[:4000])


def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def parse(xml_text):
    items = []
    root = ET.fromstring(xml_text.encode("utf-8"))
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = it.findtext("link") or ""
        m = re.search(r"/status/(\d+)", link)
        if not m:
            continue
        if title.startswith("RT by") or title.startswith("R to"):
            continue
        text = clean(it.findtext("description")) or title
        items.append((int(m.group(1)), text, f"https://x.com/i/status/{m.group(1)}"))
    return items


def fetch(user):
    for inst in NITTER:
        try:
            r = requests.get(f"{inst}/{user}/rss", timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "<rss" in r.text[:500]:
                return parse(r.text)
        except Exception:
            continue
    return None


def gemini_is_job(text):
    if not GEMINI_KEY:
        return False
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        body = {"contents": [{"parts": [{"text":
                "هل هذه التغريدة إعلان وظيفة أو توظيف؟ أجب بكلمة واحدة فقط: yes أو no.\n\n" + text}]}]}
        r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=30).json()
        ans = r["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
        return ans.startswith("yes")
    except Exception as e:
        print("gemini error", e)
        return False


def is_job(text):
    low = text.lower()
    if any(k.lower() in low for k in KEYWORDS):
        return True
    return gemini_is_job(text)


def handle_command(state, text):
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    arg = parts[1].lstrip("@").strip() if len(parts) > 1 else ""
    if cmd == "/add" and arg:
        state["accounts"].setdefault(arg, 0)
        return f"تمت إضافة @{arg}"
    if cmd == "/remove" and arg:
        state["accounts"].pop(arg, None)
        return f"تم حذف @{arg}"
    if cmd == "/list":
        return "\n".join("@" + u for u in state["accounts"]) or "ما فيه حسابات"
    return "الأوامر: /add @حساب  /remove @حساب  /list"


def process_commands(state):
    r = tg("getUpdates", offset=state["offset"], timeout=0)
    for u in r.get("result", []):
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        text = msg.get("text", "")
        if msg.get("from", {}).get("id") == ADMIN_ID and text.startswith("/"):
            send(ADMIN_ID, handle_command(state, text))


def check_accounts(state):
    accounts = state["accounts"]
    failed = 0
    for user in list(accounts):
        last_id = accounts[user]
        items = fetch(user)
        if items is None:
            failed += 1
            continue
        if not items:
            continue
        items.sort(key=lambda x: x[0])
        if not last_id:
            accounts[user] = items[-1][0]
            continue
        for tid, text, link in items:
            if tid <= last_id:
                continue
            if is_job(text):
                send(CHANNEL_ID, f"📢 @{user}\n\n{text}\n\n🔗 {link}")
                time.sleep(3)
            accounts[user] = tid
    if accounts and failed == len(accounts):
        if not state["alerted"]:
            send(ADMIN_ID, "⚠️ كل مصادر التغريدات ما تشتغل الحين. بعيد المحاولة تلقائيًا.")
            state["alerted"] = True
    else:
        state["alerted"] = False


if __name__ == "__main__":
    st = load_state()
    process_commands(st)
    check_accounts(st)
    save_state(st)
