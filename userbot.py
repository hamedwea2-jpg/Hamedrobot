"""
userbot.py — یوزربات مدیریت گروه‌های خودی
------------------------------------------------
قابلیت‌ها:
  1) ری‌اکشن خودکار روی پیام‌های گروه‌های وایت‌لیست‌شده
     - با تاخیر تصادفی بین min_delay و max_delay ثانیه (قابل تنظیم با دستور)
     - هر کاربر حداکثر یک ری‌اکشن در هر بازه cooldown (پیش‌فرض ۲۴ ساعت)
     - ایموجی‌ها از لیست مجاز همان گروه خوانده می‌شود (available_reactions)
  2) پاسخ خودکار در پی‌وی، فقط به کسی که خودش اول پیام داده، فقط یک بار
  3) دستورات مدیریتی از داخل حساب خودت (پیام به Saved Messages یا هرجا)

اجرا: python userbot.py   (بعد از ساخت سشن با login_bot.py)
"""

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime

from telethon import TelegramClient, events, functions, types, utils
from telethon.sessions import StringSession
from telethon.errors import (FloodWaitError, MessageIdInvalidError,
                           PeerFloodError, ReactionInvalidError, RPCError)

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
# در Render فایل‌سیستم ephemeral است؛ تنها مسیری که بین دیپلوی‌ها می‌ماند
# همان mount path دیسک است. با DATA_DIR می‌شود data را آن‌جا گذاشت.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE, "data")
SESSION_FILE = os.path.join(DATA_DIR, "session.txt")
DB_PATH = os.path.join(DATA_DIR, "state.db")

os.makedirs(DATA_DIR, exist_ok=True)


# ----------------------------- تنظیمات -----------------------------
class Config:
    def __init__(self, path):
        self.path = path
        self.load()

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            self.d = json.load(f)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False, indent=2)

    def __getitem__(self, k):
        return self.d[k]


CFG = Config(CONFIG_PATH)

# کلیدهای جدید با مقدار پیش‌فرض؛ اگر کانفیگ قدیمی باشد خودکار کامل می‌شود.
_DEFAULTS = {
    "reaction": {
        "enabled": True, "min_delay": 20, "max_delay": 30,
        "per_user_cooldown_hours": 24, "settle_delay": 0,
        # ۰ = نامحدود: هیچ پیامی دور ریخته نمی‌شود و هیچ عضوی جا نمی‌ماند
        "max_age_minutes": 0, "max_queue": 0, "workers": 1,
        # window_hours: پنجرهٔ کاری. پیامی که بیشتر از این در صف مانده
        # از «حالت فعال» خارج می‌شود (عضو حذف نمی‌شود؛ با پیام تازه‌اش
        # دوباره وارد می‌شود). ۰ = بدون پنجره.
        "window_hours": 12,
        # group_ttl_hours: «زمان پاکسازی خودکار» گروه‌ها. پیامی که تا آن
        # موقع ری‌اکشن نگیرد، خودِ تلگرام پاکش می‌کند؛ پس تلاش برای آن
        # هدر دادن سهمیه است. ۰ = گروه‌ها پاکسازی خودکار ندارند.
        "group_ttl_hours": 24,
        # بعد از هر FloodWait هر دو عدد `.delay` این‌قدر ثانیه بالا می‌روند
        # و در کانفیگ ذخیره می‌شوند؛ تا رسیدن به max_delay_cap ادامه دارد.
        "auto_bump_delay": 1,
        "max_delay_cap": 300,
        # حداقل فاصلهٔ دو ری‌اکشن در *یک گروه*. تلگرام به درخواست‌های
        # پشت‌سرهم در یک گروه حساس‌تر است؛ این کار ری‌اکشن‌ها را بین
        # گروه‌ها پخش می‌کند. ۰ = خاموش.
        "per_chat_gap": 30,
        # هر چند ساعت یک‌بار گروه‌های فعال دوباره چک شوند؛ اگر ری‌اکشن
        # گروهی بسته شده باشد از لیست بیرون می‌رود. ۰ = چک دوره‌ای خاموش.
        "reaction_check_hours": 6,
    },
    "pm_autoreply": {
        "enabled": False, "once_per_user": True, "reply_to_admins": False,
        "text": "سلام 👋 پیامت رسید، به زودی جواب می‌دم.",
    },
    # افزودن خودکار گروه: هر گروه تازه‌ای که پیامی از آن برسد، بدون
    # نیاز به `.addchat` به لیست اضافه می‌شود.
    "auto_add_chats": {
        "enabled": True,         # خاموش/روشن
        "min_members": 5,        # حداقل تعداد اعضا برای افزودن خودکار
        "max_per_day": 200,      # سقف روزانه (جلوگیری از انفجار)
        "exclude": [],           # گروه‌هایی که هیچ‌وقت خودکار اضافه نشوند
    },
}


def dedupe_allowed_chats():
    """تکراری‌های allowed_chats را پاک می‌کند (نسخه‌های قبلی می‌توانستند
    یک گروه را چند بار اضافه کنند). ترتیب حفظ می‌شود."""
    lst = CFG.d.get("allowed_chats") or []
    seen, out = set(), []
    for cid in lst:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    if len(out) != len(lst):
        CFG.d["allowed_chats"] = out
        CFG.save()
        return len(lst) - len(out)
    return 0


def ensure_defaults():
    changed = False
    for section, defaults in _DEFAULTS.items():
        cur = CFG.d.setdefault(section, {})
        for k, v in defaults.items():
            if k not in cur:
                cur[k] = v
                changed = True
    CFG.d.setdefault("allowed_chats", [])
    CFG.d.setdefault("admins", [])
    if changed:
        CFG.save()


ensure_defaults()
dedupe_allowed_chats()

API_ID = int(CFG["api_id"])
API_HASH = CFG["api_hash"]
OWNER_ID = int(CFG.d.get("owner_id") or 0)  # اگر 0 باشد هنگام استارت خودکار پر می‌شود

def _load_session():
    """سشن را می‌خواند. هنگام import کرش نمی‌کند تا run.py بتواند منتظر بماند.

    اولویت با متغیر محیطی `SESSION_STR` است. در Render فایل‌سیستم ephemeral
    است، پس `data/session.txt` با هر دیپلوی پاک می‌شود؛ متغیر محیطی تنها
    راه نگه‌داشتن سشن بدون دیسک پولی است.
    """
    env = (os.environ.get("SESSION_STR") or "").strip()
    if env:
        return env
    if not os.path.exists(SESSION_FILE):
        return ""
    with open(SESSION_FILE, encoding="utf-8") as f:
        return f.read().strip()


SESSION_STR = _load_session()

# اگر مستقیم اجرا شود و سشن نباشد، پیام واضح بده و خارج شو
if not SESSION_STR and __name__ == "__main__":
    raise SystemExit("❌ سشن پیدا نشد. اول login_bot.py را اجرا کن و /login بزن.")

client = TelegramClient(StringSession(SESSION_STR), API_ID, API_HASH)


# ---------------------- لاگ تمیز ----------------------
# تلگرام گاهی آپدیت‌های خراب می‌فرستد و Telethon برای هرکدام یک خط
# «Telegram is having internal issues» چاپ می‌کند. در لاگ واقعی ۱۳۳ خط از
# ۱۵۲ خط همین بود و ری‌اکشن‌ها گم می‌شدند. این‌ها خطای ربات نیستند؛ فقط
# نویز. فیلترشان می‌کنیم تا لاگ فقط کارهای خودمان را نشان بدهد.
_LOG_NOISE = (
    "internal issues",
    "Persistent timestamp outdated",
    "PersistentTimestampOutdatedError",
    "Could not find the input entity",
    "Server sent a very new message with ID",
    "Unknown request",
    "Request was unsuccessful",
)


class _QuietLogs(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(n in msg for n in _LOG_NOISE)


for _name in ("telethon", "telethon.network", "telethon.client.updates",
              "telethon.client.telegramclient"):
    logging.getLogger(_name).addFilter(_QuietLogs())
logging.getLogger("telethon").setLevel(logging.ERROR)


_last_chat_react = {}    # chat_id -> آخرین زمانی که در آن گروه ری‌اکشن زدیم

# ------------------- بسته بودن ری‌اکشن هر گروه -------------------
# دو جا در تلگرام ری‌اکشن بسته می‌شود:
#   ۱) available_reactions روی ChatReactionsNone  (مدیر کل ری‌اکشن را بسته)
#   ۲) default_banned_rights.send_reactions = True  (ممنوعیت پیش‌فرض اعضا)
# هر دو را چک می‌کنیم. نتیجه کش می‌شود تا برای هر پیام یک درخواست شبکه نرود.
_react_cache = {}        # chat_id -> (ok: bool, reason: str, at: ts)
_REACT_TTL = 21600       # ۶ ساعت


async def reactions_allowed(chat_id, force=False):
    """آیا در این گروه اصلاً می‌شود ری‌اکشن زد؟"""
    now = time.time()
    hit = _react_cache.get(chat_id)
    if hit and not force and now - hit[2] < _REACT_TTL:
        return hit[0], hit[1]
    ok, reason = True, ""
    try:
        entity = await client.get_entity(chat_id)
        full = None
        # پرانتز لازم است: `await client(...).full_chat` به‌شکل
        # `await (client(...).full_chat)` تجزیه می‌شود و کروتین هرگز
        # await نمی‌شود — یعنی چک همیشه «باز» برمی‌گشت.
        if isinstance(entity, types.Channel):
            res = await client(functions.channels.GetFullChannelRequest(entity))
            full = getattr(res, "full_chat", None)
        elif isinstance(entity, types.Chat):
            res = await client(functions.messages.GetFullChatRequest(entity.id))
            full = getattr(res, "full_chat", None)

        av = getattr(full, "available_reactions", None)
        if isinstance(av, types.ChatReactionsNone):
            ok, reason = False, "ری‌اکشن در گروه بسته است"

        rights = getattr(entity, "default_banned_rights", None)
        if ok and rights is not None and getattr(rights, "send_reactions", False):
            ok, reason = False, "ری‌اکشن برای اعضا ممنوع است"
    except FloodWaitError:
        raise
    except Exception as e:
        # اگر نتوانستیم بفهمیم، گروه را نگه می‌داریم. حذف اشتباه یک گروه
        # فعال خیلی بدتر از یکی دو ری‌اکشن هدررفته است.
        print(f"[react-check] {chat_id}: {e}")
        return True, ""

    _react_cache[chat_id] = (ok, reason, now)
    return ok, reason


async def drop_chat(chat_id, why):
    """گروه را از لیست فعال‌ها بیرون می‌برد و به اونر خبر می‌دهد."""
    lst = list(CFG.d.get("allowed_chats") or [])
    if chat_id not in lst:
        return False
    lst.remove(chat_id)
    CFG.d["allowed_chats"] = lst
    CFG.save()
    _chat_meta.pop(chat_id, None)
    _react_cache.pop(chat_id, None)
    title = chat_title(chat_id)
    print(f"[react-off] ❌ {title} ({chat_id}) از لیست بیرون رفت — {why}")
    try:
        await _notify_owner(
            f"🚫 **گروه از لیست بیرون رفت**" + chr(10) + chr(10)
            + f"👥 `{title}`" + chr(10)
            + f"🆔 `{chat_id}`" + chr(10)
            + f"📌 دلیل: {why}" + chr(10) + chr(10)
            + f"اگر دوباره باز شد: `.addchat` داخل همان گروه"
        )
    except Exception as e:
        print(f"[react-off-notify] {e}")
    return True


def chat_title(cid):
    """نام گروه برای لاگ — بدون درخواست شبکه."""
    try:
        cache = getattr(client, "_entity_cache", None)
        ent = cache.get(cid) if cache is not None else None
    except Exception:
        ent = None
    return getattr(ent, "title", None) or str(cid)


# ----------------------------- دیتابیس -----------------------------
db = sqlite3.connect(DB_PATH, check_same_thread=False)
# WAL: نوشتن‌ها سریع‌تر و بدون قفل طولانی روی ایونت‌لوپ
try:
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
except Exception:
    pass
db.execute(
    """CREATE TABLE IF NOT EXISTS reacted (
        chat_id INTEGER, user_id INTEGER, ts REAL,
        PRIMARY KEY (chat_id, user_id))"""
)
db.execute("CREATE INDEX IF NOT EXISTS idx_reacted_ts ON reacted(ts)")
db.execute("CREATE TABLE IF NOT EXISTS pm_sent (user_id INTEGER PRIMARY KEY, ts REAL)")
db.commit()


_db_lock = asyncio.Lock()


async def mark_reacted_async(chat_id, user_id):
    """نسخهٔ امن برای چند کارگر موازی."""
    async with _db_lock:
        mark_reacted(chat_id, user_id)


def reacted_recently(chat_id, user_id, hours):
    row = db.execute(
        "SELECT ts FROM reacted WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    ).fetchone()
    if not row:
        return False
    return (time.time() - row[0]) < hours * 3600


def mark_reacted(chat_id, user_id):
    db.execute(
        "INSERT OR REPLACE INTO reacted VALUES (?,?,?)", (chat_id, user_id, time.time())
    )
    db.commit()


def pm_already_sent(user_id):
    return db.execute(
        "SELECT 1 FROM pm_sent WHERE user_id=?", (user_id,)
    ).fetchone() is not None


def mark_pm_sent(user_id):
    db.execute("INSERT OR REPLACE INTO pm_sent VALUES (?,?)", (user_id, time.time()))
    db.commit()


# ------------------- ایموجی‌های مجاز هر گروه (کش) -------------------
_emoji_cache = {}       # chat_id -> (emojis, fetched_at)
_EMOJI_TTL = 3600


async def allowed_emojis(chat_id):
    now = time.time()
    hit = _emoji_cache.get(chat_id)
    if hit and now - hit[1] < _EMOJI_TTL:
        return hit[0]

    emojis = list(CFG["reaction"]["fallback_emojis"])
    try:
        entity = await client.get_entity(chat_id)
        if isinstance(entity, types.Channel):
            full = await client(functions.channels.GetFullChannelRequest(entity))
            av = full.full_chat.available_reactions
            if isinstance(av, types.ChatReactionsSome):
                got = [
                    r.emoticon
                    for r in av.reactions
                    if isinstance(r, types.ReactionEmoji)
                ]
                if got:
                    emojis = got
            elif isinstance(av, types.ChatReactionsAll):
                # همه ری‌اکشن‌های فعال تلگرام
                res = await client(functions.messages.GetAvailableReactionsRequest(0))
                got = [r.reaction for r in res.reactions if not r.inactive]
                if got:
                    emojis = got
            elif isinstance(av, types.ChatReactionsNone):
                emojis = []
    except FloodWaitError as e:
        # کارگر باید زمان اعلام‌شده را صبر کند؛ با ایموجی جایگزین ادامه نده.
        await record_flood(e.seconds, "خواندن ایموجی‌های گروه")
        raise
    except Exception as e:
        print(f"[emoji] {chat_id}: {e}")

    _emoji_cache[chat_id] = (emojis, now)
    return emojis


# ----------------------------- صف ری‌اکشن -----------------------------
queue = asyncio.Queue()

# دو ایندکس روی یک صف:
#   _pending_reactions : (chat_id, msg_id) -> (user_id, enqueued_at)
#                        برای دیدن آپدیت حذف پیام
#   _latest_of_user    : (chat_id, user_id) -> (msg_id, enqueued_at)
#                        برای ادغام: هر کاربر در هر بازه کول‌داون فقط یک
#                        ری‌اکشن می‌گیرد، پس فقط جدیدترین پیامش کار می‌شود.
#
# صف به‌صورت پیش‌فرض *نامحدود* است: هیچ پیامی دور ریخته نمی‌شود تا هیچ عضوی
# جا نیفتد. پیام‌های ردشده (کول‌داون / ادغام / حذف) صفر ثانیه هزینه دارند،
# پس صفِ بزرگ به‌خودی‌خود باعث کندی نمی‌شود.
_pending_reactions = {}
_latest_of_user = {}
_gen = 0

# آمار صف (برای .doctor و .stats)
_queue_stats = {"enqueued": 0, "deduped": 0, "expired": 0, "dropped": 0,
                "stale": 0, "sent": 0, "skipped": 0, "failed": 0,
                "capped": 0}


def _rc_cfg():
    rc = CFG["reaction"]
    lo, hi = int(rc.get("min_delay", 20)), int(rc.get("max_delay", 30))
    if hi < lo:
        lo, hi = hi, lo
    return {
        "enabled": bool(rc.get("enabled", True)),
        "min_delay": lo,
        "max_delay": hi,
        "cooldown": float(rc.get("per_user_cooldown_hours", 24)),
        "max_age": float(rc.get("max_age_minutes", 0)),
        "max_queue": int(rc.get("max_queue", 0)),
        "workers": max(1, int(rc.get("workers", 1))),
        "group_ttl": float(rc.get("group_ttl_hours", 24)),
        "window": float(rc.get("window_hours", 12)),
        "bump": int(rc.get("auto_bump_delay", 1) or 0),
        "cap": int(rc.get("max_delay_cap", 300) or 0),
        "chat_gap": float(rc.get("per_chat_gap", 30) or 0),
    }


_limit_cache = (0.0, 0)     # (محاسبه‌شده در، مقدار) — هر ۲ ثانیه تازه می‌شود
_cap_notified = 0.0

# آخرین عضوهای ردشده به‌خاطر پر بودن سقف. این فقط برای *گزارش* است
# (`.doctor`) — عمداً پیام قدیمی‌شان را دوباره وارد صف نمی‌کنیم، چون آن
# پیام تا موقع خالی شدن صف از پنجرهٔ ۱۲ ساعته بیرون رفته و کارگر ردش
# می‌کند؛ یعنی فقط صف را شلوغ می‌کرد بدون هیچ فایده‌ای.
#
# راه درستِ برگشت، خودِ عضو است: هر عضو در هر گروه فعال هر وقت پیام
# *تازه* بدهد و جایی باز باشد، وارد می‌شود. هیچ‌کس برای همیشه حذف
# نمی‌شود — حذف‌شدنی در کار نیست.
_waiting = {}               # (chat_id, user_id) -> (msg_id, ts)
_WAITING_MAX = 5000


def active_limit(refresh=False):
    """چند عضو در ۱۲ ساعت (پنجره) ری‌اکشن می‌گیرند؟ = ظرفیت × پنجره.

    این همان «سقف خودکار» است: به‌جای یک عدد ثابت، از `.delay` و `.workers`
    حساب می‌شود. با فاصلهٔ ۲۰–۳۰ ثانیه و یک کارگر: ۱٬۷۲۸ نفر.
    """
    global _limit_cache
    now = time.time()
    if not refresh and now - _limit_cache[0] < 2.0:
        return _limit_cache[1]
    rc = _rc_cfg()
    v = 0 if rc["window"] <= 0 else capacity_per_day() * rc["window"] / 24.0
    _limit_cache = (now, v)
    return v


async def can_admit(chat_id, user_id, msg_id=None):
    """آیا این عضو اجازه دارد وارد مجموعهٔ کاری بشود؟

    این همان چیزی است که «سقف خودکار» را *اجرایی* می‌کند. تا پیش از این
    `active_limit()` فقط در `.doctor` و `.window` نمایش داده می‌شد و هیچ
    جایی جلوی ورود کسی را نمی‌گرفت — برای همین مجموعهٔ کاری از سقف رد می‌شد
    (۳٬۵۴۴ نفر در برابر سقف ۳٬۴۵۶).

    قاعده: اگر عضو *از قبل* جای دارد، پیام تازه‌اش جایگزین می‌شود (جای
    اضافی نمی‌گیرد). اگر عضو تازه است و مجموعه پر است، رد می‌شود تا یک نفر
    ری‌اکشنش را بگیرد و جایش باز شود.
    """
    lim = active_limit()
    if lim <= 0:
        return True                      # پنجره خاموش = بدون سقف
    if (chat_id, user_id) in _latest_of_user:
        return True                      # از قبل جای دارد
    if len(_latest_of_user) < lim:
        return True                      # هنوز جا هست

    _queue_stats["capped"] += 1
    _waiting[(chat_id, user_id)] = (msg_id, time.time())
    if len(_waiting) > _WAITING_MAX:
        # قدیمی‌ترین‌ها را دور بریز (نه تصادفی) تا صف نوبت حفظ شود
        for k in sorted(_waiting, key=lambda k: _waiting[k][1])[
                :len(_waiting) - _WAITING_MAX]:
            _waiting.pop(k, None)

    global _cap_notified
    now = time.time()
    if now - _cap_notified > 3600:
        _cap_notified = now
        try:
            await _notify_owner(
                f"🎯 **مجموعهٔ کاری پر است**" + chr(10) + chr(10)
                + f"👥 `{len(_latest_of_user):,}` عضو در انتظار"
                f" (سقف `{lim:,.0f}`)" + chr(10)
                + f"🚫 عضوهای تازه فعلاً رد می‌شوند تا جایشان باز شود."
                + chr(10) + chr(10)
                + "هر ری‌اکشن یک جا آزاد می‌کند و نفر بعدی وارد می‌شود."
                + chr(10) + "برای سقف بزرگ‌تر: `.delay` را کمتر یا "
                  "`.workers` را بیشتر کن."
            )
        except Exception as e:
            print(f"[cap-notify] {e}")
    return False


def active_set():
    """عضوهایی که *الان* در پنجرهٔ کاری‌اند (پیامشان تازه است).

    بقیه عضوها حذف نمی‌شوند — فقط از حالت فعال بیرون‌اند و به محض اینکه
    پیام تازه‌ای بدهند دوباره وارد می‌شوند. پس صف همیشه به اندازهٔ
    `active_limit()` می‌ماند و هر ری‌اکشن یک نفر تازه را وارد می‌کند.
    """
    rc = _rc_cfg()
    if rc["window"] <= 0:
        return len(_latest_of_user), len(_latest_of_user)
    cutoff = time.time() - rc["window"] * 3600
    fresh = sum(1 for _, ts in _latest_of_user.values() if ts >= cutoff)
    return fresh, len(_latest_of_user)


def capacity_per_day():
    """حداکثر ری‌اکشن در روز = (تعداد کارگر × ۸۶۴۰۰) ÷ میانگین فاصله."""
    rc = _rc_cfg()
    avg = (rc["min_delay"] + rc["max_delay"]) / 2.0
    if avg <= 0:
        return 0
    return rc["workers"] * 86400.0 / avg


# ---------------------- افزودن خودکار گروه ----------------------
_chat_meta = {}          # cid -> {"at": ts, "by": "auto"|"manual", "members": n}
_auto_added_today = []   # timestamp ها
# گروه‌هایی که *همین حالا* در میانهٔ افزودن‌اند. بدون این، چند پیامِ
# هم‌زمانِ یک گروه تازه همه از چک «قبلاً اضافه شده؟» رد می‌شدند (چون بین
# آن چک و افزودن واقعی یک await هست) و یک گروه ده‌ها بار اضافه می‌شد.
_adding_chats = set()


def _ac_cfg():
    d = CFG.d.get("auto_add_chats") or {}
    return {
        "enabled": bool(d.get("enabled", True)),
        "min_members": int(d.get("min_members") or 0),
        "max_per_day": int(d.get("max_per_day") or 0),
        "exclude": set(d.get("exclude") or []),
    }


def _is_allowed(cid):
    return cid in set(CFG.d.get("allowed_chats") or [])


async def maybe_auto_add_chat(event, chat, chat_id):
    """اگر افزودن خودکار روشن باشد، گروه تازه را به لیست اضافه می‌کند.

    فیلترها: بلک‌لیست، حداقل اعضا، سقف روزانه.
    """
    if chat_id is None or _is_allowed(chat_id):
        return False
    if chat_id in _adding_chats:
        return False              # یک پیام دیگر همین حالا دارد اضافه‌اش می‌کند
    ac = _ac_cfg()
    if not ac["enabled"]:
        return False
    if chat_id in ac["exclude"]:
        print(f"[autochat] skip {chat_id}: در بلک‌لیست است")
        return False

    # از اینجا تا افزودن واقعی چند await هست؛ پس *همین حالا* و به‌صورت
    # هم‌زمان (بدون await) علامت می‌زنیم تا پیام‌های موازی رد شوند.
    _adding_chats.add(chat_id)
    try:
        return await _do_auto_add(event, chat, chat_id, ac)
    finally:
        _adding_chats.discard(chat_id)


async def _do_auto_add(event, chat, chat_id, ac):
    # گروهی که ری‌اکشنش بسته است به لیست اضافه نشود.
    try:
        ok, why = await reactions_allowed(chat_id, force=True)
        if not ok:
            print(f"[autochat] skip {chat_id}: {why}")
            return False
    except FloodWaitError:
        return False

    members = getattr(chat, "participants_count", None)
    if members is None:
        # سوپرگروه/کانال «channel» است و با GetFullChatRequest کار نمی‌کند
        # («Invalid object ID for a chat ... megagroups are channels»).
        # باید هر دو نوع را جدا صدا زد.
        try:
            entity = await client.get_entity(chat_id)
            if isinstance(entity, types.Channel):
                res = await client(
                    functions.channels.GetFullChannelRequest(entity))
            else:
                res = await client(
                    functions.messages.GetFullChatRequest(entity.id))
            full = getattr(res, "full_chat", None)
            members = getattr(full, "participants_count", 0)
        except FloodWaitError:
            raise
        except Exception as e:
            print(f"[autochat] {chat_id}: تعداد اعضا خوانده نشد ({e})")
            members = 0
    members = members or 0

    if ac["min_members"] and members and members < ac["min_members"]:
        print(f"[autochat] skip {chat_id}: {members} عضو < {ac['min_members']}")
        return False

    cutoff = time.time() - 86400
    _auto_added_today[:] = [t for t in _auto_added_today if t > cutoff]
    if ac["max_per_day"] and len(_auto_added_today) >= ac["max_per_day"]:
        print(f"[autochat] سقف روزانه ({ac['max_per_day']}) پر است")
        return False

    lst = list(CFG.d.get("allowed_chats") or [])
    if chat_id in lst:                      # دفاع در برابر تکراریِ مانده
        return False
    lst.append(chat_id)
    CFG.d["allowed_chats"] = lst
    CFG.save()

    now = time.time()
    _chat_meta[chat_id] = {"at": now, "by": "auto", "members": members}
    _auto_added_today.append(now)
    title = getattr(chat, "title", None) or str(chat_id)

    print(f"[autochat] ✅ {chat_id} ({title}) — {members} عضو")
    try:
        await _notify_owner(
            f"➕ **گروه خودکار اضافه شد**" + chr(10) + chr(10)
            + f"👥 `{title}`" + chr(10)
            + f"🆔 `{chat_id}`" + chr(10)
            + f"👤 `{members}` عضو" + chr(10)
            + f"📊 امروز `{len(_auto_added_today)}` گروه اضافه شده" + chr(10) + chr(10)
            + f"حذف: `.delchat {chat_id}`" + chr(10)
            + f"بلک دائمی: `.blockchat {chat_id}`" + chr(10)
            + f"خاموش کردن: `.autochat off`"
        )
    except Exception as e:
        print(f"[autochat-notify] {e}")
    return True


# ---------------------- گزارش FloodWait به اونر ----------------------
_flood_history = []          # (زمان، ثانیه، تنظیم فعلی)
_last_flood_alert = 0.0
_FLOOD_ALERT_GAP = 300       # حداکثر یک پیام در هر ۵ دقیقه


async def record_flood(seconds, where):
    """هر FloodWait را ثبت می‌کند، `.delay` را خودکار یکی بالا می‌برد و به
    اونر پیام می‌دهد.

    هدف: ربات خودش از منطقهٔ خطر عقب بنشیند، بدون اینکه منتظر دست تو باشد.
    """
    global _last_flood_alert
    rc = _rc_cfg()
    now = time.time()
    _flood_history.append((now, seconds, rc["min_delay"], rc["max_delay"],
                           rc["workers"], where))
    del _flood_history[:-60:]

    # --- بالا بردن خودکار `.delay` ---
    old_lo, old_hi = rc["min_delay"], rc["max_delay"]
    step, cap = rc["bump"], rc["cap"]
    bumped = ""
    if step > 0:
        new_lo, new_hi = old_lo + step, old_hi + step
        if cap:
            new_lo, new_hi = min(new_lo, cap), min(new_hi, cap)
        if (new_lo, new_hi) != (old_lo, old_hi):
            CFG["reaction"]["min_delay"] = new_lo
            CFG["reaction"]["max_delay"] = new_hi
            CFG.save()
            rc = _rc_cfg()
            bumped = (f"🔼 خودکار زیاد شد: `.delay {old_lo} {old_hi}` "
                      f"← `.delay {new_lo} {new_hi}`")
            if cap and new_hi >= cap:
                bumped += f"  (سقف `{cap}` — دیگر بالا نمی‌رود)"
            print(f"[auto-bump] delay {old_lo}-{old_hi} -> {new_lo}-{new_hi}")

    print(f"[flood] {seconds}s at {where} "
          f"(delay {rc['min_delay']}-{rc['max_delay']}s, {rc['workers']} worker)")

    if not OWNER_ID:
        return
    if now - _last_flood_alert < _FLOOD_ALERT_GAP:
        return                    # پشت‌سرهم پیام نمی‌دهیم
    _last_flood_alert = now

    recent = [h for h in _flood_history if now - h[0] < 3600]
    lines = [
        f"⚠️ **FloodWait — تلگرام محدود کرد**",
        "",
        f"⏳ مدت: `{seconds}` ثانیه (`{fmt_age(seconds)}`)",
        f"📍 کجا: `{where}`",
        f"⚙️ تنظیم قبل: `.delay {old_lo} {old_hi}` با `{rc['workers']}` کارگر",
    ]
    if bumped:
        lines += ["", bumped,
                  f"✅ تنظیم الان: `.delay {rc['min_delay']} {rc['max_delay']}`"]
    else:
        lo, hi = int(rc["min_delay"] * 1.5), int(rc["max_delay"] * 1.5)
        lines += ["", f"👈 پیشنهاد: `.delay {lo} {hi}`"]
    lines += [f"📊 در ۱ ساعت اخیر: `{len(recent)}` بار"]
    if len(recent) >= 3:
        lines += ["", "🔴 پشت‌سرهم flood می‌خوری. کارگرها را کم کن: "
                      f"`.workers {max(1, rc['workers'] - 1)}`"]
    lines += ["", "تاریخچهٔ کامل: `.flood`"]
    try:
        await _notify_owner(chr(10).join(lines))
    except Exception as e:
        print(f"[flood-notify-err] {e}")


async def _notify_owner(text):
    """پیام مستقیم به اونر (بدون محدودیت ادمین و once_per_user)."""
    await client.send_message(OWNER_ID, text)


def pending_users():
    """تعداد *عضوهای* واقعاً در انتظار.

    `queue.qsize()` همهٔ پیام‌های خام را می‌شمارد، از جمله چند پیام از یک
    کاربر که با پیام تازه‌ترِ همان کاربر جایگزین شده‌اند. آن‌ها در صف
    می‌مانند ولی کارگر بدون مصرف زمان ردشان می‌کند. پس عدد معنادار برای
    «چند نفر منتظرند» همین است، نه qsize.
    """
    return len(_latest_of_user)


def queue_line():
    qn, un = queue.qsize(), pending_users()
    cap = _rc_cfg()["max_queue"]
    tail = "" if cap <= 0 else f" / سقف `{cap}`"
    if qn > un:
        return (f"صف: `{qn}` پیام خام{tail} — از آن `{un}` **عضو** واقعاً "
                f"در انتظارند\n     (`{qn - un}` پیام تکراریِ همان عضوهاست که "
                f"بدون مصرف زمان رد می‌شوند)")
    return f"صف: `{qn}`{tail}"


def coverage_estimate():
    """چند نفر از عضوهای در انتظار *قبل از پاکسازی خودکار گروه* ری‌اکشن
    می‌گیرند؟ بقیه پیام‌هایشان را تلگرام پاک می‌کند و هرگز ری‌اکشن نمی‌گیرند.

    برمی‌گرداند: (تعداد کل، تعداد نجات‌یافته، ثانیه‌های باقی‌ماندهٔ قدیمی‌ترین)
    """
    rc = _rc_cfg()
    ttl = rc["group_ttl"] * 3600.0
    ages = sorted(time.time() - ts for _, ts in _latest_of_user.values())
    if not ages:
        return 0, 0, 0.0
    if ttl <= 0:
        return len(ages), len(ages), ages[0]
    avg = (rc["min_delay"] + rc["max_delay"]) / 2.0
    workers = max(1, rc["workers"])
    saved = 0
    for i, age in enumerate(ages):
        # کارگرها موازی‌اند، پس نوبت i-ام حدود (i ÷ workers) × avg بعد می‌رسد
        if age + avg * (i / workers) <= ttl:
            saved += 1
        else:
            break
    return len(ages), saved, ages[0]


def fmt_age(seconds):
    """۹۰ -> `1m30s` | ۵۴۰۰ -> `1h30m` | ۹۰۰۰۰ -> `1d1h`"""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def eta_hours(n):
    cap = capacity_per_day()
    if cap <= 0:
        return 0.0
    return n / cap * 24.0


def _drop_oldest(max_queue):
    """صف که پر شد، قدیمی‌ترین‌ها دور ریخته می‌شوند.

    ری‌اکشن روی پیام تازه ارزش دارد؛ روی پیامی که ده دقیقه پیش ارسال شده نه.
    بدون این سقف، صف بی‌نهایت بزرگ می‌شد و ربات عملاً «هنگ» می‌کرد.
    """
    overflow = queue.qsize() - max_queue
    if overflow <= 0:
        return 0
    victims = sorted(_pending_reactions.items(), key=lambda kv: kv[1][1])[:overflow]
    victim_ids = set()
    for (chat_id, msg_id), (user_id, ts) in victims:
        victim_ids.add((chat_id, msg_id))
        _pending_reactions.pop((chat_id, msg_id), None)
        key = (chat_id, user_id)
        cur = _latest_of_user.get(key)
        if cur and cur[0] == msg_id:
            _latest_of_user.pop(key, None)

    # خودِ صف فیزیکی هم باید کوچک شود، وگرنه qsize دروغ می‌گوید و کارگر
    # مجبور است هزاران آیتم مرده را یکی‌یکی بردارد.
    kept = []
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        queue.task_done()
        if (item[0], item[1]) not in victim_ids:
            kept.append(item)
    for item in kept:
        queue.put_nowait(item)

    _queue_stats["dropped"] += len(victim_ids)
    return len(victim_ids)


def enqueue_reaction(chat_id, user_id, msg_id):
    """یک پیام را برای ری‌اکشن در صف می‌گذارد. بدون هیچ درخواست شبکه.

    هیچ پیامی اینجا دور ریخته نمی‌شود؛ فقط «جدیدترین پیام هر کاربر» نشان
    گذاری می‌شود و کارگر بقیه را بدون مصرف زمان رد می‌کند. این‌طور هم صف
    کوچک می‌ماند و هم هیچ پیامی گم نمی‌شود.
    """
    rc = _rc_cfg()
    now = time.time()
    key = (chat_id, user_id)

    prev = _latest_of_user.get(key)
    if prev is not None and prev[0] != msg_id:
        # پیام قبلی همین کاربر جای خودش را به این پیام می‌دهد. اگر در
        # _pending_reactions نگهش داریم، آن دیکشنری با هر پیام رشد می‌کرد
        # (۳۸۷ هزار پیام = ~۸۰ مگابایت حافظهٔ بی‌مصرف). آیتم قبلی در صف
        # فیزیکی می‌ماند ولی کارگر بدون مصرف زمان ردش می‌کند.
        _pending_reactions.pop((chat_id, prev[0]), None)
        _queue_stats["deduped"] += 1

    _pending_reactions[(chat_id, msg_id)] = (user_id, now)
    _latest_of_user[key] = (msg_id, now)
    queue.put_nowait((chat_id, msg_id, user_id, now))
    _queue_stats["enqueued"] += 1
    if rc["max_queue"] > 0:
        _drop_oldest(rc["max_queue"])
    return True


def prune_expired():
    """آیتم‌های بیات (قدیمی‌تر از max_age) از انتظار خارج می‌شوند."""
    max_age = _rc_cfg()["max_age"] * 60.0
    if max_age <= 0:
        return 0
    cutoff = time.time() - max_age
    n = 0
    for (chat_id, msg_id), (user_id, ts) in list(_pending_reactions.items()):
        if ts < cutoff:
            del _pending_reactions[(chat_id, msg_id)]
            key = (chat_id, user_id)
            cur = _latest_of_user.get(key)
            if cur and cur[0] == msg_id:
                _latest_of_user.pop(key, None)
            n += 1
    if n:
        _queue_stats["expired"] += n
    return n


def oldest_pending_age():
    if not _pending_reactions:
        return 0.0
    return time.time() - min(ts for _, ts in _pending_reactions.values())


def forget_message(chat_id, msg_id):
    """خروج یک پیام از انتظار (حذف شده یا پردازش شده)."""
    ent = _pending_reactions.pop((chat_id, msg_id), None)
    if ent is None:
        return
    key = (chat_id, ent[0])
    cur = _latest_of_user.get(key)
    if cur and cur[0] == msg_id:
        _latest_of_user.pop(key, None)


def is_current(chat_id, user_id, msg_id):
    """آیا این پیام هنوز جدیدترین پیامِ در انتظارِ همین کاربر است؟"""
    cur = _latest_of_user.get((chat_id, user_id))
    return cur is not None and cur[0] == msg_id


@client.on(events.MessageDeleted())
async def on_message_deleted(event):
    """استفاده از آپدیت حذف تلگرام، بدون درخواست get_messages یا polling."""
    if event.chat_id is not None:
        for msg_id in event.deleted_ids:
            forget_message(event.chat_id, msg_id)
    else:
        # حذف پیام گروه معمولی ممکن است بدون chat_id برسد. شناسه‌های کانال
        # مستقل‌اند؛ حذف بدون chat_id نباید پیام هم‌شماره در سوپرگروه را حذف کند.
        deleted_ids = set(event.deleted_ids)
        for key in tuple(_pending_reactions):
            if (key[1] in deleted_ids
                    and utils.resolve_id(key[0])[1] is types.PeerChat):
                forget_message(key[0], key[1])


async def reaction_worker(worker_id=1):
    """هر بار یک ری‌اکشن، با فاصله تصادفی بین min_delay و max_delay.

    می‌شود چند نمونهٔ موازی از این کارگر بالا آورد (`.workers N`) تا ظرفیت
    ضرب شود؛ هر کارگر فاصلهٔ خودش را رعایت می‌کند.
    """
    tag = f"[w{worker_id}]"
    while True:
        chat_id, msg_id, user_id, enqueued_at = await queue.get()
        rc = _rc_cfg()
        sent = False
        try:
            # حذف شده، یا پیام تازه‌تری از همین کاربر جایش را گرفته
            if (chat_id, msg_id) not in _pending_reactions:
                continue
            if not is_current(chat_id, user_id, msg_id):
                _queue_stats["deduped"] += 1
                continue
            if not rc["enabled"]:
                continue
            # پیام بیات: ری‌اکشن روی پیامی که ده دقیقه پیش ارسال شده هم
            # بی‌ارزش است هم الگوی رباتی می‌سازد.
            if rc["max_age"] > 0 and time.time() - enqueued_at > rc["max_age"] * 60:
                _queue_stats["stale"] += 1
                continue
            # پنجرهٔ کاری: پیامی که بیشتر از `window_hours` در صف مانده
            # «قابل اقدام» نیست. تا الان این فقط در `.doctor` و `.window`
            # گزارش می‌شد و کارگر اصلاً نگاهش نمی‌کرد — یعنی عملاً همهٔ صف
            # به‌ترتیب FIFO ری‌اکشن می‌گرفتند و پنجره هیچ اثری نداشت.
            # عضو حذف نمی‌شود؛ با پیام تازه‌اش دوباره وارد می‌شود.
            if rc["window"] > 0 and time.time() - enqueued_at > rc["window"] * 3600:
                _queue_stats["stale"] += 1
                continue
            if reacted_recently(chat_id, user_id, rc["cooldown"]):
                continue
            # اگر پیام آن‌قدر قدیمی است که تا رسیدن ما گروه پاکش می‌کند،
            # درخواست نفرست — هم سهمیه هدر می‌رود هم MessageIdInvalidError.
            # فقط پیامی را رد می‌کنیم که *همین حالا* از عمرش گذشته باشد.
            # حدس زدن دربارهٔ آینده (اینکه «تا نوبت این پیام برسد دیر می‌شود»)
            # عملاً همه را رد می‌کرد، چون صف همیشه بزرگ است.
            if rc["group_ttl"] > 0 and time.time() - enqueued_at > rc["group_ttl"] * 3600:
                _queue_stats["stale"] += 1
                continue

            # حداقل فاصله در یک گروه. در لاگ واقعی، ۴ ری‌اکشن پشت‌سرهم
            # به یک گروه می‌رفت — همان الگویی که تلگرام زود flood می‌گیرد.
            # با این کار ری‌اکشن‌ها بین گروه‌ها پخش می‌شوند.
            if rc["chat_gap"] > 0:
                waited = time.time() - _last_chat_react.get(chat_id, 0.0)
                if waited < rc["chat_gap"]:
                    await asyncio.sleep(rc["chat_gap"] - waited)

            # گروهی که ری‌اکشنش بسته است اصلاً درخواست نده — هم سهمیه
            # هدر می‌رود هم ReactionInvalidError پشت‌سرهم می‌خورد.
            ok, why = await reactions_allowed(chat_id)
            if not ok:
                _queue_stats["stale"] += 1
                continue

            emojis = await allowed_emojis(chat_id)
            if not emojis:
                continue
            emo = random.choice(emojis)

            # ممکن است هنگام دریافت ایموجی‌ها خبر حذف برسد.
            if (chat_id, msg_id) not in _pending_reactions:
                continue

            await client(
                functions.messages.SendReactionRequest(
                    peer=chat_id,
                    msg_id=msg_id,
                    reaction=[types.ReactionEmoji(emoticon=emo)],
                    big=False,
                )
            )
            await mark_reacted_async(chat_id, user_id)
            sent = True
            _queue_stats["sent"] += 1
            _last_chat_react[chat_id] = time.time()
            print(f"[react] {emo}  {chat_title(chat_id)}  ({chat_id})")

        except FloodWaitError as e:
            await record_flood(e.seconds, f"ری‌اکشن {tag}")
            await asyncio.sleep(e.seconds + 5)
        except MessageIdInvalidError:
            # پیام پیش از ری‌اکشن حذف شده (مثلاً پاکسازی خودکار گروه).
            # ری‌اکشنی ارسال نشده، پس دلیلی برای صبر کردن نیست — این همان
            # چیزی است که باعث می‌شد ۳۸۷ هزار پیام حذف‌شده هرکدام ۸ تا ۱۶
            # ثانیه از وقت ربات را بخورند. یک مکث خیلی کوتاه می‌گذاریم تا
            # پشت‌سرهم درخواست ناموفق نرویم.
            _queue_stats["stale"] += 1
            await asyncio.sleep(0.4)
        except ReactionInvalidError:
            # ری‌اکشن رد شد: یا ایموجی مجاز نیست یا گروه کلاً بسته است.
            # کش را خالی کن و دوباره بپرس؛ اگر واقعاً بسته بود، بیرونش ببر.
            _emoji_cache.pop(chat_id, None)
            ok, why = await reactions_allowed(chat_id, force=True)
            if not ok:
                await drop_chat(chat_id, why or "ری‌اکشن رد شد")
            sent = True
            _queue_stats["failed"] += 1
        except RPCError as e:
            print(f"[react-err] {tag} {e}")
            sent = True
            _queue_stats["failed"] += 1
        except Exception as e:
            print(f"[react-err] {tag} {e}")
            sent = True
            _queue_stats["failed"] += 1
        finally:
            forget_message(chat_id, msg_id)
            queue.task_done()

        # فقط بعد از یک درخواست واقعی به تلگرام صبر می‌کنیم. اگر آیتم رد شده
        # بود (بیات / کول‌داون / حذف / ادغام) صبر کردن فقط صف را بزرگ‌تر
        # می‌کرد — همان چیزی که صف را به ده‌ها هزار می‌رساند و ربات را
        # «هنگ» نشان می‌داد.
        if sent:
            await asyncio.sleep(random.uniform(rc["min_delay"], rc["max_delay"]))
        else:
            _queue_stats["skipped"] += 1
            await asyncio.sleep(0)


# ----------------------------- هندلر گروه -----------------------------
@client.on(events.NewMessage(incoming=True))
async def on_group_message(event):
    if event.is_private:
        return
    if not CFG["reaction"]["enabled"]:
        return

    chat_id = event.chat_id
    if chat_id not in CFG["allowed_chats"]:
        # افزودن خودکار: اولین پیام از یک گروه تازه، خودش آن را ثبت می‌کند.
        if not await maybe_auto_add_chat(event, event.chat, chat_id):
            return

    uid = event.sender_id
    if uid is None or uid == OWNER_ID:
        return
    if reacted_recently(chat_id, uid, CFG["reaction"]["per_user_cooldown_hours"]):
        return

    # اگر فرستنده در کش entity نبود (اتفاق رایج بعد از لاگین تازه)،
    # یک بار از شبکه می‌گیریم؛ وگرنه عضو بی‌صدا رد می‌شد.
    sender = event.sender
    if sender is None:
        try:
            sender = await event.get_sender()
        except Exception:
            return
    if not isinstance(sender, types.User) or sender.bot:
        return

    # مکث کوتاه پیش از ورود به صف: اگر پیام در همین لحظه حذف شود، خبر حذف
    # زودتر از ورود به صف می‌رسد و اصلاً صف اشغال نمی‌شود.
    settle = max(0.0, float(CFG["reaction"].get("settle_delay", 0)))
    if settle:
        await asyncio.sleep(settle)
        if not CFG["reaction"]["enabled"] or chat_id not in CFG["allowed_chats"]:
            return

    # سقف خودکار: اگر مجموعهٔ کاری پر است، عضو تازه وارد نمی‌شود تا یک نفر
    # ری‌اکشنش را بگیرد و جایش باز شود. (عضوی که از قبل جای دارد رد
    # نمی‌شود؛ پیام تازه‌اش فقط جایگزین قبلی می‌شود.)
    if not await can_admit(chat_id, uid, event.id):
        return

    # از اینجا به بعد فقط کارگر تصمیم می‌گیرد؛ همه بررسی‌های نهایی
    # (حذف، بیات بودن، کول‌داون، جایگزینی با پیام تازه‌تر) آن‌جا انجام می‌شود.
    enqueue_reaction(chat_id, uid, event.id)


# ----------------------------- پاسخ خودکار پی‌وی -----------------------------
_pm_inflight = set()
_PM_LOG_MAX = 40
_pm_log = []          # آخرین تصمیم‌های پی‌وی (برای .pmlog)


def pm_note(uid, verdict, detail=""):
    """هر تصمیم پی‌وی را ثبت می‌کند تا با .pmlog قابل دیدن باشد."""
    line = f"{datetime.now().strftime('%H:%M:%S')} | {uid} | {verdict}"
    if detail:
        line += f" | {detail}"
    _pm_log.append(line)
    if len(_pm_log) > _PM_LOG_MAX:
        del _pm_log[:-_PM_LOG_MAX]
    print("[pm] " + line)


@client.on(events.NewMessage(incoming=True))
async def on_private_message(event):
    if not event.is_private:
        return
    pm = CFG["pm_autoreply"]
    uid = event.sender_id
    if uid is None:
        return

    if not pm.get("enabled"):
        pm_note(uid, "رد", "پاسخ خودکار خاموش است (.pm on)")
        return

    # ادمین‌ها/اونر به‌صورت پیش‌فرض پاسخ خودکار نمی‌گیرند (چون خودشان دستور
    # می‌دهند). اگر می‌خواهی خودت هم جواب بگیری: .pmadmins on
    if (uid == OWNER_ID or uid in admin_ids()) and not pm.get("reply_to_admins", False):
        pm_note(uid, "رد", "ادمین/اونر است (.pmadmins on)")
        return

    # پیام‌های دستوری هرگز پاسخ خودکار نمی‌گیرند
    if (event.raw_text or "").strip().startswith("."):
        pm_note(uid, "رد", "پیام دستوری است")
        return

    # 🔧 باگ اصلی: event.sender برای کسی که اولین بار پی‌وی می‌دهد None است،
    # چون در کش entity تلگرام نیست. قبلاً اینجا بی‌صدا return می‌شد و هیچ
    # پی‌وی‌ای جواب نمی‌گرفت. حالا اگر در کش نبود، یک بار از شبکه می‌گیریم.
    sender = event.sender
    if sender is None:
        try:
            sender = await event.get_sender()
        except Exception as e:
            pm_note(uid, "رد", f"get_sender خطا داد: {e}")
            return
    if sender is None:
        pm_note(uid, "رد", "فرستنده ناشناخته است")
        return
    if getattr(sender, "bot", False):
        pm_note(uid, "رد", "فرستنده ربات است")
        return

    if pm.get("once_per_user", True) and pm_already_sent(uid):
        pm_note(uid, "رد", "قبلاً جواب گرفته (once_per_user)")
        return
    if uid in _pm_inflight:          # دو پیام پشت سرهم = یک پاسخ
        pm_note(uid, "رد", "پاسخ قبلی هنوز در راه است")
        return

    _pm_inflight.add(uid)
    try:
        await asyncio.sleep(random.uniform(1.5, 4.0))
        await event.respond(pm["text"], link_preview=False)
        mark_pm_sent(uid)
        pm_note(uid, "✅ ارسال شد", "")
    except FloodWaitError as e:
        pm_note(uid, "❌ FloodWait", f"{e.seconds} ثانیه محدودیت")
        await record_flood(e.seconds, "پاسخ خودکار پی‌وی")
    except PeerFloodError:
        pm_note(uid, "❌ PeerFlood",
                "تلگرام این اکانت را از پیام دادن به غیرمخاطب‌ها محدود کرده")
    except Exception as e:
        pm_note(uid, "❌ خطا", f"{type(e).__name__}: {e}")
    finally:
        _pm_inflight.discard(uid)


# ----------------------------- دستورات مدیریتی -----------------------------
HELP = """**📖 راهنمای کامل یوزربات HeavenCloud**

دستورها را یا با **اکانت خودت** در هر چتی بنویس (پیام ادیت می‌شود)،
یا از اکانت **ادمین** در **پی‌وی یوزربات** بفرست (جواب می‌گیری).
همه دستورها با نقطه `.` شروع می‌شوند و همه تغییرات در `config.json` ذخیره می‌شود.

━━━━━━━━━━━━━━━━━━
**⏱ ۱) فاصله زمانی ری‌اکشن**

`.delay 20 30`
بین هر دو ری‌اکشن یک عدد تصادفی بین ۲۰ تا ۳۰ ثانیه صبر می‌کند.
تصادفی بودن عمدی است — فاصله ثابت الگوی رباتی می‌سازد و لو می‌رود.

`.delay` بدون عدد → فاصله فعلی را نشان می‌دهد.
کمتر از ۳ ثانیه پذیرفته نمی‌شود (خطر محدودیت اکانت).

━━━━━━━━━━━━━━━━━━
**🔁 ۲) کنترل ری‌اکشن**

`.react on` روشن کردن
`.react off` خاموش کردن (صف حفظ می‌شود)

`.cooldown 24`
هر کاربر در هر گروه فقط **یک بار در هر ۲۴ ساعت** ری‌اکشن می‌گیرد،
نه روی همه پیام‌هایش. عدد را می‌توانی هر چند ساعت بگذاری.
این آمار در دیتابیس ذخیره می‌شود و با ری‌استارت پاک نمی‌شود.

`.emojis`
ایموجی‌های مجاز **همین گروه** را نشان می‌دهد. ربات خودکار از لیست
مجاز هر گروه می‌خواند؛ اگر گروه محدودیتی نداشته باشد از کل
ری‌اکشن‌های فعال تلگرام استفاده می‌کند.

━━━━━━━━━━━━━━━━━━
**👥 ۳) مدیریت گروه‌ها**

`.addchat` ← **داخل گروه** بزن تا آن گروه فعال شود
`.delchat` ← داخل گروه بزن تا غیرفعال شود
`.chats` ← لیست گروه‌های فعال

ربات **فقط** در گروه‌های این لیست کار می‌کند. یک بار در هر گروه
خودت `.addchat` بزن و تمام.

━━━━━━━━━━━━━━━━━━
**✉️ ۴) پاسخ خودکار پی‌وی**

`.pm on` / `.pm off`
`.pmtext متن دلخواه`  ← تنظیم متن (چند خطی هم می‌شود)
`.pmshow` ← نمایش متن فعلی
`.pmreset` ← پاک کردن لیست کسانی که قبلاً جواب گرفته‌اند
`.pmtest` ← تست: وضعیت + متن فعلی را نشان می‌دهد
`.pmadmins on` ← خودت/ادمین‌ها هم پاسخ خودکار بگیرند

فقط به کسی جواب می‌دهد که **خودش اول پیام داده**، و برای هر نفر
فقط **یک بار**. ربات‌ها و خودت مستثنی هستند.

⚠️ اگر خودت پی‌وی می‌زنی و جواب نمی‌گیری، طبیعی است: ادمین/اونر
مستثنی است. با `.pmadmins on` یا `.pmtest` بررسی کن.

━━━━━━━━━━━━━━━━━━
**🚦 ۵) صف، ظرفیت و سلامت**

`.doctor` ← سلامت کامل + ظرفیت + زمان رسیدن به همهٔ صف
`.workers` ← ظرفیت فعلی و زمان تخلیه صف
`.workers 3` ← سه کارگر موازی → ظرفیت سه برابر
`.queue clear` ← خالی کردن کامل صف
`.maxqueue 0` ← **۰ = نامحدود** (پیش‌فرض). عدد بگذاری سقف می‌شود
`.maxage 0` ← **۰ = بدون محدودیت قدمت** (پیش‌فرض)

**صف به‌صورت پیش‌فرض نامحدود است:** هیچ پیامی دور ریخته نمی‌شود و
**هیچ عضوی جا نمی‌ماند**. هر عضو در هر بازهٔ کول‌داون دقیقاً یک ری‌اکشن
می‌گیرد.

**ولی ظرفیت فیزیکی محدود است:**

```
ظرفیت روزانه = تعداد کارگر × ۸۶۴۰۰ ÷ میانگین فاصله
```

| فاصله | ۱ کارگر | ۳ کارگر |
|---|---|---|
| ۲۰–۳۰s | ۳٬۴۵۶ | ۱۰٬۳۶۸ |
| ۸–۱۵s | ۷٬۵۱۳ | ۲۲٬۵۳۹ |
| ۳–۵s | ۲۱٬۶۰۰ | ۶۴٬۸۰۰ |

اگر صف بزرگ‌تر از ظرفیت یک روز شود، `.doctor` هشدار می‌دهد. در آن حالت
`.workers` را بالا ببر یا `.delay` را کم کن.

━━━━━━━━━━━━━━━━━━
**🛡 ۶) ادمین‌ها**

`.admins` ← لیست ادمین‌ها
`.addadmin 123456789` ← افزودن (یا روی پیام کسی ریپلای کن)
`.deladmin 123456789` ← حذف

ادمین‌ها می‌توانند از پی‌وی یوزربات دستور بدهند. اونر قابل حذف نیست.

━━━━━━━━━━━━━━━━━━
**📊 ۷) وضعیت و ابزار**

`.ping` ← زنده بودن + تعداد پیام در صف
`.stats` ← آمار کامل: ری‌اکشن کل، ۲۴ ساعت اخیر، پی‌وی، تنظیمات
`.id` ← آیدی عددی خودت و این چت
`.help` ← همین راهنما

━━━━━━━━━━━━━━━━━━
**⚠️ نکات ایمنی**

• فاصله ۲۰–۳۰ ثانیه انتخاب امنی است، پایین‌تر نبر.
• فقط در گروه‌های خودت فعالش کن.
• `data/session.txt` = دسترسی کامل به اکانتت. هرگز share نکن.
• اگر تلگرام FloodWait بدهد، ربات خودکار صبر می‌کند — نگران نباش.
"""


def admin_ids():
    """اونر + هر آیدی که در admins کانفیگ باشد."""
    ids = set(CFG.d.get("admins") or [])
    if OWNER_ID:
        ids.add(OWNER_ID)
    return ids


def _cmd_filter(e):
    """دستور یا از خود اکانت (outgoing) یا از یک ادمین در پی‌وی."""
    if e.out:
        return True
    return e.is_private and e.sender_id in admin_ids()


def owner_cmd(pattern):
    return events.NewMessage(pattern=pattern, func=_cmd_filter)


async def say(e, text):
    """جواب را به‌صورت یک پیام تازه می‌فرستد.

    قبلاً اگر دستور را خودِ اونر زده بود، ربات پیامِ خودِ او را ادیت می‌کرد و
    متن دستور ناپدید می‌شد. حالا در پی‌وی `respond` می‌زند (پیام مستقل، بدون
    هدرِ ریپلای) و در گروه `reply` (تا معلوم باشد جواب کدام دستور است). در هر
    دو حالت پیام اصلی دست‌نخورده می‌ماند.
    """
    send = e.respond if e.is_private else e.reply
    try:
        return await send(text, link_preview=False)
    except Exception:
        try:
            return await e.reply(text, link_preview=False)
        except Exception as err:
            print(f"[say-err] {err}")


@client.on(owner_cmd(r"^\.help$"))
async def _help(e):
    await say(e, HELP)


@client.on(owner_cmd(r"^\.ping$"))
async def _ping(e):
    t = time.time()
    m = await say(e, "🏓 ...")
    txt = (f"🏓 **Pong!** `{(time.time()-t)*1000:.0f} ms`\n"
           f"در انتظار: `{pending_users()}` عضو "
           f"(`{queue.qsize()}` پیام خام در صف)")
    if m:
        try:
            await m.edit(txt)
        except Exception:
            pass


@client.on(owner_cmd(r"^\.delay(?:\s+(\d+)\s+(\d+))?$"))
async def _delay(e):
    lo, hi = e.pattern_match.group(1), e.pattern_match.group(2)
    rc = CFG["reaction"]
    if lo is None:
        return await say(e, 
            f"⏱ فاصله فعلی: **{rc['min_delay']} تا {rc['max_delay']}** ثانیه\n"
            f"تغییر: `.delay 20 30`"
        )
    lo, hi = int(lo), int(hi)
    if lo > hi:
        lo, hi = hi, lo
    if lo < 3:
        return await say(e, "⚠️ کمتر از ۳ ثانیه خطر محدودیت دارد. عدد بزرگ‌تری بده.")
    rc["min_delay"], rc["max_delay"] = lo, hi
    CFG.save()
    lim = active_limit(refresh=True)
    tail = f"\n🎯 سقف خودکار شد: `{lim:,.0f}` عضو در پنجرهٔ ۱۲ ساعته" if lim else ""
    await say(e, f"✅ فاصله ری‌اکشن روی **{lo} تا {hi}** ثانیه تنظیم شد." + tail)


@client.on(owner_cmd(r"^\.react\s+(on|off)$"))
async def _react(e):
    v = e.pattern_match.group(1) == "on"
    CFG["reaction"]["enabled"] = v
    CFG.save()
    await say(e, f"{'✅ ری‌اکشن روشن شد.' if v else '⏸ ری‌اکشن خاموش شد.'}")


@client.on(owner_cmd(r"^\.cooldown\s+(\d+)$"))
async def _cooldown(e):
    h = int(e.pattern_match.group(1))
    CFG["reaction"]["per_user_cooldown_hours"] = h
    CFG.save()
    await say(e, f"✅ هر کاربر حداکثر یک ری‌اکشن در هر **{h} ساعت**.")


@client.on(owner_cmd(r"^\.emojis$"))
async def _emojis(e):
    emos = await allowed_emojis(e.chat_id)
    if not emos:
        return await say(e, "❌ ری‌اکشن در این گروه غیرفعال است.")
    await say(e, f"😀 **{len(emos)}** ایموجی مجاز:\n" + " ".join(emos))


@client.on(owner_cmd(r"^\.addchat$"))
async def _addchat(e):
    if e.is_private:
        return await say(e, "❌ این دستور را داخل گروه بزن.")
    cid = e.chat_id
    if cid in CFG["allowed_chats"]:
        return await say(e, "ℹ️ این گروه از قبل در لیست است.")
    CFG["allowed_chats"].append(cid)
    CFG.save()
    _chat_meta[cid] = {"at": time.time(), "by": "manual",
                       "members": getattr(e.chat, "participants_count", 0) or 0}
    await say(e, f"✅ گروه اضافه شد.\n`{cid}`")


@client.on(owner_cmd(r"^\.delchat$"))
async def _delchat(e):
    cid = e.chat_id
    if cid not in CFG["allowed_chats"]:
        return await say(e, "ℹ️ این گروه در لیست نبود.")
    CFG["allowed_chats"].remove(cid)
    CFG.save()
    await say(e, "🗑 گروه از لیست حذف شد.")


@client.on(owner_cmd(r"^\.chats$"))
async def _chats(e):
    ids = CFG["allowed_chats"]
    ac = _ac_cfg()
    if not ids:
        return await say(e, "لیست خالی است. داخل گروه `.addchat` بزن "
                            "یا بگذار افزودن خودکار انجامش بدهد.")
    lines = []
    for cid in ids:
        try:
            ent = await client.get_entity(cid)
            title = getattr(ent, "title", cid)
        except Exception:
            title = "(نامشخص)"
        meta = _chat_meta.get(cid) or {}
        tag = "🤖" if meta.get("by") == "auto" else "✋"
        mem = meta.get("members") or 0
        st = _react_cache.get(cid)
        if st is None:
            rst = "❔"
        elif st[0]:
            rst = "🟢"
        else:
            rst = f"🚫 {st[1]}"
        lines.append(f"{tag} {rst} {title} — `{cid}` ({mem} عضو)")
    cutoff = time.time() - 86400
    today = len([t for t in _auto_added_today if t > cutoff])
    state = "🟢 روشن" if ac["enabled"] else "🔴 خاموش"
    header = (f"👥 **گروه‌های فعال ({len(ids)}):**\n\n"
              f"🤖 افزودن خودکار: {state} | امروز `{today}` گروه اضافه شده\n"
              f"حداقل اعضا: `{ac['min_members']}` | سقف روزانه: `{ac['max_per_day']}`"
              f" | بلک‌لیست: `{len(ac['exclude'])}`\n\n"
              "🟢 ری‌اکشن باز | 🚫 بسته | ❔ هنوز چک نشده  → `.chatcheck`\n\n")
    await say(e, header + "\n".join(lines))


@client.on(owner_cmd(r"^\.chatcheck$"))
async def _chatcheck(e):
    """همهٔ گروه‌های فعال را همین حالا چک می‌کند؛ بسته‌ها بیرون می‌روند."""
    await say(e, "🔎 در حال چک کردن ری‌اکشن گروه‌ها…")
    dropped, closed, total = await recheck_chats()
    lines = ["✅ **چک کامل شد**", ""]
    if dropped:
        for cid in dropped:
            lines.append(f"🚫 `{chat_title(cid)}` (`{cid}`) بیرون رفت")
    else:
        lines.append("همهٔ گروه‌ها ری‌اکشنشان باز است.")
    lines += ["", f"👥 گروه‌های فعال: `{total}`",
              f"⏱ چک خودکار هر `{CFG['reaction'].get('reaction_check_hours', 6)}` ساعت"]
    await say(e, "\n".join(lines))


@client.on(owner_cmd(r"^\.autochat(?:\s+(on|off|\d+))?$"))
async def _autochat(e):
    """افزودن خودکار گروه: on / off / N (حداقل اعضا)."""
    arg = (e.pattern_match.group(1) or "").strip()
    CFG.d.setdefault("auto_add_chats", {})
    if arg == "on":
        CFG.d["auto_add_chats"]["enabled"] = True
        CFG.save()
        return await say(e, "✅ افزودن خودکار گروه **روشن** شد. هر گروه تازه‌ای "
                            "که پیامی از آن برسد، خودش اضافه می‌شود.")
    if arg == "off":
        CFG.d["auto_add_chats"]["enabled"] = False
        CFG.save()
        return await say(e, "🔴 افزودن خودکار **خاموش** شد. از این پس فقط با "
                            "`.addchat` گروه اضافه می‌شود.")
    if arg.isdigit():
        CFG.d["auto_add_chats"]["min_members"] = int(arg)
        CFG.save()
        return await say(e, f"✅ حداقل اعضا برای افزودن خودکار: `{arg}`")
    ac = _ac_cfg()
    cutoff = time.time() - 86400
    today = len([t for t in _auto_added_today if t > cutoff])
    lines = [
        "🤖 **افزودن خودکار گروه**", "",
        f"وضعیت: {'🟢 روشن' if ac['enabled'] else '🔴 خاموش'}",
        f"حداقل اعضا: `{ac['min_members']}`",
        f"سقف روزانه: `{ac['max_per_day']}`",
        f"بلک‌لیست: `{len(ac['exclude'])}` گروه",
        f"امروز اضافه شده: `{today}`",
        f"کل گروه‌های فعال: `{len(CFG['allowed_chats'])}`",
        "",
        "✋ `.autochat on` / `.autochat off`",
        "🔢 `.autochat 10` ← حداقل ۱۰ عضو",
        "🚫 `.blockchat` ← این گروه هرگز خودکار اضافه نشود",
    ]
    await say(e, "\n".join(lines))


@client.on(owner_cmd(r"^\.blockchat(?:\s+(\d+))?$"))
async def _blockchat(e):
    """گروه را در بلک‌لیست افزودن خودکار می‌گذارد."""
    arg = (e.pattern_match.group(1) or "").strip()
    cid = int(arg) if arg else e.chat_id
    if e.is_private and not arg:
        return await say(e, "❌ داخل گروه بزن یا آیدی را بده: `.blockchat 123456`")
    CFG.d.setdefault("auto_add_chats", {}).setdefault("exclude", [])
    ex = CFG.d["auto_add_chats"]["exclude"]
    if cid in ex:
        return await say(e, f"ℹ️ `{cid}` از قبل در بلک‌لیست است.")
    ex.append(cid)
    CFG.save()
    await say(e, f"🚫 `{cid}` در بلک‌لیست گذاشته شد — دیگر خودکار اضافه نمی‌شود.\n"
                 f"برای برگرداندن: `.unblockchat {cid}`")


@client.on(owner_cmd(r"^\.unblockchat(?:\s+(\d+))?$"))
async def _unblockchat(e):
    arg = (e.pattern_match.group(1) or "").strip()
    cid = int(arg) if arg else e.chat_id
    ex = (CFG.d.get("auto_add_chats") or {}).get("exclude") or []
    if cid not in ex:
        return await say(e, f"ℹ️ `{cid}` در بلک‌لیست نبود.")
    ex.remove(cid)
    CFG.save()
    await say(e, f"✅ `{cid}` از بلک‌لیست بیرون آمد.")


@client.on(owner_cmd(r"^\.pm\s+(on|off)$"))
async def _pm(e):
    v = e.pattern_match.group(1) == "on"
    CFG["pm_autoreply"]["enabled"] = v
    CFG.save()
    await say(e, f"{'✅ پاسخ خودکار پی‌وی روشن شد.' if v else '⏸ خاموش شد.'}")


@client.on(owner_cmd(r"^\.pmtext\s+([\s\S]+)$"))
async def _pmtext(e):
    CFG["pm_autoreply"]["text"] = e.pattern_match.group(1).strip()
    CFG.save()
    await say(e, "✅ متن پاسخ خودکار ذخیره شد.")


@client.on(owner_cmd(r"^\.pmshow$"))
async def _pmshow(e):
    await say(e, "✉️ **متن فعلی:**\n\n" + CFG["pm_autoreply"]["text"])


@client.on(owner_cmd(r"^\.pmreset$"))
async def _pmreset(e):
    db.execute("DELETE FROM pm_sent")
    db.commit()
    await say(e, "🗑 لیست دریافت‌کنندگان پاسخ خودکار پاک شد.")


@client.on(owner_cmd(r"^\.stats$"))
async def _stats(e):
    rc = CFG["reaction"]
    total = db.execute("SELECT COUNT(*) FROM reacted").fetchone()[0]
    today = db.execute(
        "SELECT COUNT(*) FROM reacted WHERE ts > ?", (time.time() - 86400,)
    ).fetchone()[0]
    pms = db.execute("SELECT COUNT(*) FROM pm_sent").fetchone()[0]
    rcc = _rc_cfg()
    cap = capacity_per_day()
    uniq = len(_latest_of_user)
    lines = [
        "📊 **آمار**", "",
        f"ری‌اکشن کل: `{total}`",
        f"۲۴ ساعت اخیر: `{today}`",
        f"پاسخ پی‌وی: `{pms}`",
        "",
        queue_line() + (" (نامحدود)" if rcc["max_queue"] <= 0 else ""),
        f"قدیمی‌ترین در صف: `{fmt_age(oldest_pending_age())}`",
        "",
        f"🚀 ظرفیت: `{cap:,.0f}` ری‌اکشن در روز "
        f"({rcc['workers']} کارگر × فاصله {rcc['min_delay']}-{rcc['max_delay']}s)",
        f"⏳ زمان رسیدن به همهٔ عضوها: `{fmt_age(eta_hours(uniq) * 3600)}`",
        "",
        f"وضعیت ری‌اکشن: {'🟢' if rc['enabled'] else '🔴'}",
        f"کول‌داون: `{rc['per_user_cooldown_hours']}h`",
        f"گروه‌ها: `{len(CFG['allowed_chats'])}`",
        f"پی‌وی: {'🟢' if CFG['pm_autoreply']['enabled'] else '🔴'}",
    ]
    await say(e, "\n".join(lines))


@client.on(owner_cmd(r"^\.doctor$"))
async def _doctor(e):
    """سلامت کامل: صف، تاخیر ایونت‌لوپ، دیتابیس، پی‌وی."""
    t0 = time.time()
    await asyncio.sleep(0.05)
    lag_ms = (time.time() - t0 - 0.05) * 1000
    s = _queue_stats
    rc = _rc_cfg()
    rows = db.execute("SELECT COUNT(*) FROM reacted").fetchone()[0]
    pmrows = db.execute("SELECT COUNT(*) FROM pm_sent").fetchone()[0]
    lag_ico = "🟢" if lag_ms < 250 else ("🟡" if lag_ms < 1000 else "🔴")
    qn = queue.qsize()
    cap = capacity_per_day()
    age = oldest_pending_age()

    if rc["max_queue"] > 0:
        q_ico = "🟢" if qn < rc["max_queue"] * 0.5 else (
            "🟡" if qn < rc["max_queue"] else "🔴")
    else:
        # صف نامحدود: معیار سلامت، عقب‌افتادگی زمانی است نه تعداد
        q_ico = "🟢" if age < 3600 else ("🟡" if age < 6 * 3600 else "🔴")

    lines = [
        "🩺 **دکتر**", "",
        f"{lag_ico} تاخیر ایونت‌لوپ: `{lag_ms:.0f} ms`",
        f"{q_ico} " + queue_line(),
        f"⏳ قدیمی‌ترین در صف: `{fmt_age(age)}`"
        + (f" (حداکثر مجاز `{fmt_age(rc['max_age']*60)}`)" if rc["max_age"] > 0
           else " (بدون محدودیت — چیزی دور ریخته نمی‌شود)"),
        f"🧵 کارگرها: `{rc['workers']}`",
        f"🚀 ظرفیت: `{cap:,.0f}` در روز",
        f"👥 عضوهای در انتظار: `{pending_users()}`"
        + (f" از سقف `{active_limit():,.0f}`"
           f"{'  🔴 پر است' if pending_users() >= active_limit() else ''}"
           if active_limit() else "  (بدون سقف)"),
        f"🎯 در پنجرهٔ {fmt_age(rc['window']*3600)} (قابل ری‌اکشن): "
        f"`{active_set()[0]}`",
        f"⏱ زمان رسیدن به همهٔ عضوها: "
        f"`{fmt_age(eta_hours(pending_users()) * 3600)}`",
        "",
        f"وارد صف: `{s['enqueued']}` | ادغام هم‌کاربر: `{s['deduped']}`",
        f"بیات ردشده: `{s['stale']}` | منقضی: `{s['expired']}` | "
        f"دور ریخته: `{s['dropped']}`",
        f"ارسال‌شده: `{s['sent']}` | ردشده بدون خواب: `{s['skipped']}` | "
        f"❌ خطا: `{s['failed']}`",
        f"🎯 ردشده به‌خاطر پر بودن سقف: `{s['capped']}`"
        + (f" (`{len(_waiting)}` عضو متفاوت)" if _waiting else "")
        + "  — حذف نمی‌شوند؛ با پیام تازه‌شان برمی‌گردند",
        "",
        f"🗄 reacted: `{rows}` ردیف | pm_sent: `{pmrows}` ردیف",
        f"🔗 اتصال: {'🟢' if client.is_connected() else '🔴'}",
        f"🚦 FloodWait: `{len(_flood_history)}` بار"
        + (f" (آخرین `{fmt_age(time.time() - _flood_history[-1][0])}` پیش)"
           if _flood_history else "") + "  → `.flood`",
    ]
    un = pending_users()
    total_p, saved_p, _age0 = coverage_estimate()
    if rc["group_ttl"] > 0 and total_p > saved_p:
        lines += ["",
                  f"⚠️ **پاکسازی خودکار گروه `{fmt_age(rc['group_ttl']*3600)}` است.** "
                  f"از `{total_p}` عضو در انتظار فقط ~`{saved_p}` نفر به موقع "
                  f"ری‌اکشن می‌گیرند؛ پیامِ ~`{total_p - saved_p}` نفر را تلگرام "
                  "پاک می‌کند.",
                  "   راه‌حل: `.workers 3` و/یا `.delay 3 6` — یا `.ttl 0` اگر "
                  "گروه‌هایت پاکسازی خودکار ندارند."]
    if cap > 0 and un > cap:
        lines += ["", "⚠️ **عضوهای در انتظار بیشتر از ظرفیت یک روز است.** "
                  f"`.workers {max(2, (un * 25 // 86400) + 1)}` یا `.delay` "
                  "کمتر بگذار."]
    await say(e, "\n".join(lines))


@client.on(owner_cmd(r"^\.queue\s+clear$"))
async def _queue_clear(e):
    n = queue.qsize()
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            break
    _pending_reactions.clear()
    _latest_of_user.clear()
    await say(e, f"🗑 صف خالی شد ({n} آیتم دور ریخته شد).")


@client.on(owner_cmd(r"^\.queue\s+compact$"))
async def _queue_compact(e):
    """پیام‌های جایگزین‌شده را از صف فیزیکی دور می‌ریزد (فقط عدد qsize را
    کوچک می‌کند؛ هیچ عضوی حذف نمی‌شود)."""
    before = queue.qsize()
    kept = []
    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        queue.task_done()
        if is_current(item[0], item[2], item[1]) and (item[0], item[1]) in _pending_reactions:
            kept.append(item)
    for item in kept:
        queue.put_nowait(item)
    await say(e, f"🧹 صف فشرده شد: `{before}` → `{queue.qsize()}` پیام خام\n"
                 f"عضوهای در انتظار دست‌نخورده: `{pending_users()}`")


@client.on(owner_cmd(r"^\.maxqueue\s+(\d+)$"))
async def _maxqueue(e):
    v = int(e.pattern_match.group(1))
    if v != 0 and v < 10:
        return await say(e, "⚠️ سقف صف حداقل ۱۰ است (یا `0` برای نامحدود).")
    CFG["reaction"]["max_queue"] = v
    CFG.save()
    if v == 0:
        return await say(e, "✅ صف **نامحدود** شد. هیچ پیامی دور ریخته نمی‌شود "
                            "و هیچ عضوی جا نمی‌ماند.")
    await say(e, f"✅ سقف صف روی `{v}` تنظیم شد.")


@client.on(owner_cmd(r"^\.workers(?:\s+(\d+))?$"))
async def _workers(e):
    """تعداد کارگرهای موازی = ظرفیت ضرب می‌شود (با رعایت فاصله هر کارگر)."""
    arg = e.pattern_match.group(1)
    rc = _rc_cfg()
    if arg is None:
        cap = capacity_per_day()
        return await say(e,
            f"🧵 کارگرهای فعال: `{rc['workers']}`\n"
            f"🚀 ظرفیت: `{cap:,.0f}` ری‌اکشن در روز\n"
            f"⏱ زمان رسیدن به همهٔ صف (`{queue.qsize()}`): "
            f"`{fmt_age(eta_hours(queue.qsize()) * 3600)}`\n\n"
            f"تغییر: `.workers 3`\n"
            f"⚠️ هر کارگر بیشتر = درخواست بیشتر به تلگرام = خطر FloodWait بالاتر. "
            f"با ۲ یا ۳ شروع کن و `.doctor` را نگاه کن.")
    n = int(arg)
    if n < 1 or n > 10:
        return await say(e, "⚠️ بین ۱ تا ۱۰.")
    CFG["reaction"]["workers"] = n
    CFG.save()
    live = sync_workers()
    newcap = capacity_per_day()
    active_limit(refresh=True)
    await say(e,
        f"✅ کارگرهای فعال: `{live}`\n"
        f"🚀 ظرفیت جدید: `{newcap:,.0f}` ری‌اکشن در روز\n"
        f"⏱ زمان رسیدن به همهٔ صف: "
        f"`{fmt_age(eta_hours(queue.qsize()) * 3600)}`\n\n"
        f"اگر در لاگ `[flood]` دیدی، کارگرها را کم کن.")


@client.on(owner_cmd(r"^\.flood$"))
async def _flood(e):
    """تاریخچهٔ FloodWait — برای اینکه بفهمی کدام `.delay` تلگرام را عصبانی می‌کند."""
    if not _flood_history:
        return await say(e, "✅ هنوز FloodWait نخورده‌ای.\n\n"
                            "هر بار که تلگرام محدود کند، همین‌جا پیام می‌گیری "
                            "با مدت و پیشنهاد `.delay`.")
    now = time.time()
    lines = ["🚦 **تاریخچهٔ FloodWait** (جدیدترین بالا)", ""]
    for ts, secs, lo, hi, w, where in reversed(_flood_history[-20:]):
        when = fmt_age(now - ts) + " پیش"
        lines.append(f"`{when}` | `{fmt_age(secs)}` | "
                     f"delay {lo}-{hi}s | {w} کارگر | {where}")
    last_h = [h for h in _flood_history if now - h[0] < 3600]
    last_d = [h for h in _flood_history if now - h[0] < 86400]
    rc = _rc_cfg()
    lines += ["",
              f"۱ ساعت اخیر: `{len(last_h)}` | ۲۴ ساعت اخیر: `{len(last_d)}`",
              f"تنظیم فعلی: `.delay {rc['min_delay']} {rc['max_delay']}` "
              f"با `{rc['workers']}` کارگر",
              "",
              "💡 **چطور تنظیم امن را پیدا کنی:** هر بار که این پیام آمد، "
              "پیشنهادش را اعمال کن. وقتی چند روز بدون FloodWait گذشت، "
              "می‌توانی کمی پایین‌تر بیایی تا حد safe خودت دستت بیاید."]
    await say(e, chr(10).join(lines))


@client.on(owner_cmd(r"^\.window(?:\s+(\d+))?$"))
async def _window(e):
    """پنجرهٔ کاری: چند عضو در این بازه ری‌اکشن می‌گیرند؟ سقفش خودکار از
    روی `.delay` و `.workers` حساب می‌شود."""
    arg = e.pattern_match.group(1)
    rc = _rc_cfg()
    if arg is None:
        fresh, total = active_set()
        lim = active_limit()
        cur = "بدون پنجره (۰)" if rc["window"] <= 0 else fmt_age(rc["window"] * 3600)
        cap = capacity_per_day()
        txt = [
            f"🪟 پنجرهٔ کاری: `{cur}`",
            f"🚀 ظرفیت: `{cap:,.0f}` در روز",
            f"🎯 سقف خودکار: `{lim:,.0f}` عضو در این پنجره" if lim else
            "🎯 سقف: بدون محدودیت",
            f"👥 الان در پنجره: `{fresh}` از `{total}` عضو در انتظار",
            "",
            "تغییر: `.window 12`  (یا `.window 0` برای برداشتن پنجره)",
        ]
        if lim and fresh < total:
            txt += ["", f"⚠️ `{total - fresh}` عضو پیامشان از پنجره گذشته. "
                        "حذف نمی‌شوند — با پیام تازه‌شان دوباره وارد می‌شوند."]
        return await say(e, "\n".join(txt))
    v = int(arg)
    if v < 0:
        return await say(e, "⚠️ `0` = بدون پنجره، عدد مثبت = ساعت.")
    CFG["reaction"]["window_hours"] = v
    active_limit(refresh=True)
    CFG.save()
    fresh, total = active_set()
    if v == 0:
        return await say(e, "✅ پنجره برداشته شد. همهٔ عضوها همیشه فعال‌اند.")
    await say(e,
        f"✅ پنجرهٔ کاری: `{v}` ساعت\n"
        f"🎯 سقف خودکار: `{active_limit():,.0f}` عضو\n"
        f"👥 الان در پنجره: `{fresh}` از `{total}`\n\n"
        f"سقف با `.delay` و `.workers` عوض می‌شود — عدد ثابت نیست.")


@client.on(owner_cmd(r"^\.ttl(?:\s+(\d+))?$"))
async def _ttl(e):
    """«زمان پاکسازی خودکار» گروه‌ها. پیامی که تا آن موقع ری‌اکشن نگیرد
    خودِ تلگرام پاکش می‌کند، پس تلاش برای آن هدر دادن سهمیه است."""
    arg = e.pattern_match.group(1)
    rc = _rc_cfg()
    total_p, saved_p, _ = coverage_estimate()
    if arg is None:
        cur = ("بدون محدودیت (۰)" if rc["group_ttl"] <= 0
               else fmt_age(rc["group_ttl"] * 3600))
        return await say(e,
            f"⏳ پاکسازی خودکار گروه‌ها: `{cur}`\n"
            f"👥 عضوهای در انتظار: `{total_p}`\n"
            f"✅ به موقع ری‌اکشن می‌گیرند: ~`{saved_p}`\n"
            f"❌ پیامشان پاک می‌شود: ~`{total_p - saved_p}`\n\n"
            f"تغییر: `.ttl 48` (یا `.ttl 0` اگر پاکسازی خودکار ندارند)")
    v = int(arg)
    if v < 0:
        return await say(e, "⚠️ عدد منفی معنا ندارد. `0` = بدون محدودیت.")
    CFG["reaction"]["group_ttl_hours"] = v
    CFG.save()
    sync_workers()          # ظرفیت/ETA عوض شد
    total_p, saved_p, _ = coverage_estimate()
    if v == 0:
        return await say(e, "✅ محدودیت پاکسازی برداشته شد. حالا حتی پیام "
                            "قدیمی هم امتحان می‌شود.")
    await say(e,
        f"✅ پاکسازی خودکار گروه‌ها: `{v}` ساعت\n"
        f"✅ به موقع ری‌اکشن می‌گیرند: ~`{saved_p}` از `{total_p}`\n"
        f"❌ پیامشان پاک می‌شود: ~`{total_p - saved_p}`\n\n"
        f"اگر تعداد دوم زیاد است: `.workers 3` یا `.delay 3 6`")


@client.on(owner_cmd(r"^\.maxage\s+(\d+)$"))
async def _maxage(e):
    v = int(e.pattern_match.group(1))
    CFG["reaction"]["max_age_minutes"] = v
    CFG.save()
    if v == 0:
        return await say(e, "✅ محدودیت قدمت برداشته شد. حتی پیام چند ساعته "
                            "هم ری‌اکشن می‌گیرد.")
    await say(e, f"✅ پیام قدیمی‌تر از `{v}` دقیقه ری‌اکشن نمی‌گیرد.")


@client.on(owner_cmd(r"^\.pmadmins\s+(on|off)$"))
async def _pmadmins(e):
    v = e.pattern_match.group(1) == "on"
    CFG["pm_autoreply"]["reply_to_admins"] = v
    CFG.save()
    await say(e, f"{'✅ ادمین‌ها/اونر هم پاسخ خودکار می‌گیرند.' if v else '⏸ ادمین‌ها/اونر پاسخ خودکار نمی‌گیرند.'}")


@client.on(owner_cmd(r"^\.pmlog$"))
async def _pmlog(e):
    """آخرین تصمیم‌های پاسخ خودکار پی‌وی — برای اینکه بفهمی چرا جواب نداد."""
    if not _pm_log:
        return await say(e, "📭 هنوز هیچ پی‌وی‌ای ثبت نشده.\n"
                            "از یک اکانت دیگر به یوزربات پیام بده و دوباره "
                            "`.pmlog` بزن.")
    body = "\n".join(f"`{x}`" for x in reversed(_pm_log[-25:]))
    await say(e, f"📜 **آخرین تصمیم‌های پی‌وی** (جدیدترین بالا)\n\n{body}\n\n"
                 f"ستون‌ها: ساعت | آیدی | تصمیم | دلیل")


@client.on(owner_cmd(r"^\.pmtest$"))
async def _pmtest(e):
    """یک پی‌وی واقعی به خودت می‌فرستد تا خطای تلگرام لو برود."""
    pm = CFG["pm_autoreply"]
    st = "🟢 روشن" if pm.get("enabled") else "🔴 خاموش — با `.pm on` روشنش کن"
    adm = "بله" if pm.get("reply_to_admins") else "نه — با `.pmadmins on`"
    once = "بله" if pm.get("once_per_user", True) else "نه"
    body = str(pm.get("text", ""))
    await say(e,
        "🧪 **تست پاسخ خودکار پی‌وی**\n\n"
        f"وضعیت: {st}\n"
        f"ادمین/اونر هم جواب بگیرند: {adm}\n"
        f"هر کاربر فقط یک بار: {once}\n\n"
        "📝 متنی که فرستاده می‌شود:\n\n" + body +
        "\n\n⏳ حالا همان متن را برایت می‌فرستم...")

    # ارسال واقعی: اگر تلگرام اجازه ندهد، دلیلش اینجا لو می‌رود
    try:
        await client.send_message(e.sender_id, body)
        await say(e, "✅ **ارسال موفق.** یعنی اکانت می‌تواند پی‌وی بفرستد.\n"
                     "اگر از اکانت دیگری جواب نمی‌گیری، `.pmlog` را نگاه کن.")
    except PeerFloodError:
        await say(e, "❌ **PeerFloodError** — تلگرام این اکانت را از پیام دادن "
                     "به غیرمخاطب‌ها محدود کرده.\n\nاین با کد حل نمی‌شود: "
                     "اکانت ریپورت شده. باید چند روز صبر کنی یا از اکانت "
                     "دیگری استفاده کنی.")
    except FloodWaitError as ex:
        await say(e, f"⏳ **FloodWait** — {ex.seconds} ثانیه محدودیت.")
    except Exception as ex:
        await say(e, f"❌ خطای تلگرام: `{type(ex).__name__}: {ex}`")


@client.on(owner_cmd(r"^\.id$"))
async def _id(e):
    uid = e.sender_id
    txt = f"🆔 **آیدی عددی تو:** `{uid}`\n**این چت:** `{e.chat_id}`"
    if not e.is_private:
        txt += f"\n**نوع:** گروه/کانال"
    await say(e, txt)


@client.on(owner_cmd(r"^\.admins$"))
async def _admins(e):
    ids = sorted(admin_ids())
    lines = []
    for i in ids:
        tag = " (اونر)" if i == OWNER_ID else ""
        lines.append(f"• `{i}`{tag}")
    await say(e, f"🛡 **ادمین‌ها ({len(ids)}):**\n" + "\n".join(lines))


@client.on(owner_cmd(r"^\.addadmin(?:\s+(\d+))?$"))
async def _addadmin(e):
    arg = e.pattern_match.group(1)
    uid = None
    if arg:
        uid = int(arg)
    else:
        rep = await e.get_reply_message()
        if rep:
            uid = rep.sender_id
    if not uid:
        return await say(e, "استفاده: `.addadmin 123456789` یا روی پیام کسی ریپلای کن.")

    admins = CFG.d.setdefault("admins", [])
    if uid in admins or uid == OWNER_ID:
        return await say(e, "ℹ️ از قبل ادمین است.")
    admins.append(uid)
    CFG.save()
    await say(e, f"✅ ادمین اضافه شد: `{uid}`")


@client.on(owner_cmd(r"^\.deladmin\s+(\d+)$"))
async def _deladmin(e):
    uid = int(e.pattern_match.group(1))
    if uid == OWNER_ID:
        return await say(e, "⛔ اونر را نمی‌توان حذف کرد.")
    admins = CFG.d.setdefault("admins", [])
    if uid not in admins:
        return await say(e, "ℹ️ در لیست ادمین‌ها نبود.")
    admins.remove(uid)
    CFG.save()
    await say(e, f"🗑 ادمین حذف شد: `{uid}`")


# ----------------------------- نگهداری دوره‌ای -----------------------------
_worker_tasks = []


def sync_workers():
    """تعداد کارگرهای فعال را با تنظیمات `workers` هم‌تراز می‌کند."""
    want = _rc_cfg()["workers"]
    _worker_tasks[:] = [t for t in _worker_tasks if not t.done()]
    while len(_worker_tasks) < want:
        wid = len(_worker_tasks) + 1
        _worker_tasks.append(asyncio.create_task(reaction_worker(wid)))
        print(f"[worker] #{wid} started (total {len(_worker_tasks)})")
    while len(_worker_tasks) > want:
        t = _worker_tasks.pop()
        t.cancel()
        print(f"[worker] stopped (total {len(_worker_tasks)})")
    return len(_worker_tasks)


async def recheck_chats(notify_each=True):
    """گروه‌های فعال را دوباره چک می‌کند؛ هرکدام ری‌اکشنش بسته شده بیرون می‌رود.

    خروجی: (بیرون‌رفته‌ها, بسته‌ها, کل).
    """
    dropped, closed = [], []
    for cid in list(CFG.d.get("allowed_chats") or []):
        try:
            ok, why = await reactions_allowed(cid, force=True)
        except FloodWaitError as e:
            print(f"[react-check] FloodWait {e.seconds}s — بقیه چک‌ها بعداً")
            break
        except Exception as e:
            print(f"[react-check] {cid}: {e}")
            continue
        if not ok:
            closed.append((cid, why))
            if await drop_chat(cid, why):
                dropped.append(cid)
        if not notify_each:
            continue
    return dropped, closed, len(CFG.d.get("allowed_chats") or [])


async def janitor(interval=300):
    """هر چند دقیقه: صف بیات را خالی می‌کند، دیتابیس را می‌روبد و گروه‌هایی
    که ری‌اکشنشان بسته شده را از لیست بیرون می‌برد."""
    last_check = 0.0
    while True:
        try:
            n = prune_expired()
            if n:
                print(f"[janitor] {n} آیتم بیات از انتظار خارج شد")
            hours = float(CFG["reaction"].get("per_user_cooldown_hours", 24))
            cutoff = time.time() - (hours + 24) * 3600
            cur = db.execute("DELETE FROM reacted WHERE ts < ?", (cutoff,))
            if cur.rowcount:
                db.commit()
                print(f"[janitor] {cur.rowcount} ردیف قدیمی reacted پاک شد")

            # چک دوره‌ای گروه‌ها: ری‌اکشنشان بسته شده؟
            every = float(CFG["reaction"].get("reaction_check_hours", 6) or 0)
            if every > 0 and time.time() - last_check >= every * 3600:
                last_check = time.time()
                dropped, closed, total = await recheck_chats()
                if dropped:
                    print(f"[janitor] {len(dropped)} گروه با ری‌اکشن بسته "
                          f"از لیست بیرون رفت")
        except Exception as e:
            print(f"[janitor-err] {e}")
        await asyncio.sleep(interval)


# ----------------------------- اجرا -----------------------------
async def main():
    global OWNER_ID
    await client.start()
    me = await client.get_me()

    # اگر owner_id در کانفیگ صفر باشد، خودکار از روی سشن پر می‌شود
    if not OWNER_ID:
        OWNER_ID = me.id
        CFG.d["owner_id"] = me.id
        CFG.save()
        print(f"ℹ️  owner_id خودکار روی {me.id} تنظیم شد.")

    print(f"✅ Userbot started as {me.first_name} ({me.id})")
    print(f"   Groups: {len(CFG['allowed_chats'])} | "
          f"Delay: {CFG['reaction']['min_delay']}-{CFG['reaction']['max_delay']}s")
    sync_workers()
    asyncio.create_task(janitor())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
