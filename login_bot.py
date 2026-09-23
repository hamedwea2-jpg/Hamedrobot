"""
login_bot.py — ربات ورود سشن
--------------------------------
این ربات فقط یک کار می‌کند: از طریق چت با ربات، شماره و کد را می‌گیرد
و یک StringSession می‌سازد و در data/session.txt ذخیره می‌کند.
بعد از آن userbot.py می‌تواند با همان سشن بالا بیاید.

فقط owner_id اجازه استفاده دارد.
اجرا:  python login_bot.py
"""

import asyncio
import json
import os
import re

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
)

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE, "data")
SESSION_FILE = os.path.join(DATA_DIR, "session.txt")

os.makedirs(DATA_DIR, exist_ok=True)

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

API_ID = int(CFG["api_id"])
API_HASH = CFG["api_hash"]
BOT_TOKEN = CFG["login_bot_token"]
OWNER_ID = int(CFG["owner_id"])

bot = TelegramClient(os.path.join(DATA_DIR, "login_bot"), API_ID, API_HASH)

# وضعیت گفتگوی ورود، در حافظه
state = {}


def is_owner(event):
    return event.sender_id == OWNER_ID


async def reset(uid):
    st = state.pop(uid, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start(event):
    if not is_owner(event):
        return await event.respond("⛔ این ربات خصوصی است.")
    await reset(event.sender_id)
    exists = os.path.exists(SESSION_FILE)
    txt = (
        "🔐 **ربات ورود سشن**\n\n"
        "برای ساخت سشن یوزربات دستور /login را بزن.\n"
        "برای دیدن وضعیت /status و برای پاک کردن سشن /logout.\n\n"
        f"وضعیت فعلی: {'✅ سشن ذخیره شده' if exists else '❌ سشنی ذخیره نشده'}"
    )
    await event.respond(txt)


@bot.on(events.NewMessage(pattern=r"^/status$"))
async def status(event):
    if not is_owner(event):
        return
    if os.path.exists(SESSION_FILE):
        size = os.path.getsize(SESSION_FILE)
        await event.respond(f"✅ سشن موجود است ({size} بایت)\nمسیر: `data/session.txt`")
    else:
        await event.respond("❌ سشنی ذخیره نشده. /login را بزن.")


@bot.on(events.NewMessage(pattern=r"^/session$"))
async def show_session(event):
    """سشن را به‌صورت متنی می‌دهد تا در Render به‌عنوان SESSION_STR بگذاری.

    در Render فایل‌سیستم ephemeral است؛ پس `data/session.txt` با هر دیپلوی
    پاک می‌شود و تنها راه مطمئن، متغیر محیطی است.
    """
    if not is_owner(event):
        return
    env = (os.environ.get("SESSION_STR") or "").strip()
    if env:
        return await event.respond(
            "ℹ️ سشن از متغیر محیطی `SESSION_STR` خوانده می‌شود "
            "(نه از فایل). همان مقدار را در داشبورد Render داری.\n\n"
            f"طولش `{len(env)}` کاراکتر است.")
    if not os.path.exists(SESSION_FILE):
        return await event.respond("❌ سشنی ذخیره نشده. /login را بزن.")
    with open(SESSION_FILE, encoding="utf-8") as f:
        txt = f.read().strip()
    if not txt:
        return await event.respond("❌ فایل سشن خالی است. /login را بزن.")
    await event.respond(
        f"🔑 **سشن** (`{len(txt)}` کاراکتر)\n\n"
        f"در Render: Environment Variables → `SESSION_STR` → همین را بگذار.\n\n"
        f"`{txt}`\n\n"
        f"⚠️ این رشته دسترسی کامل به اکانتت می‌دهد. این پیام تا ۹۰ ثانیه "
        f"دیگر حذف می‌شود.")
    await asyncio.sleep(90)
    try:
        await event.delete()
    except Exception:
        pass


@bot.on(events.NewMessage(pattern=r"^/logout$"))
async def logout(event):
    if not is_owner(event):
        return
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
        await event.respond("🗑 سشن پاک شد.")
    else:
        await event.respond("چیزی برای پاک کردن نبود.")


@bot.on(events.NewMessage(pattern=r"^/cancel$"))
async def cancel(event):
    if not is_owner(event):
        return
    await reset(event.sender_id)
    await event.respond("❌ عملیات لغو شد.")


@bot.on(events.NewMessage(pattern=r"^/login$"))
async def login(event):
    if not is_owner(event):
        return await event.respond("⛔ این ربات خصوصی است.")
    await reset(event.sender_id)
    state[event.sender_id] = {"step": "phone", "client": None}
    await event.respond(
        "📱 شماره تلفن اکانت را با کد کشور بفرست.\n"
        "مثال: `+989121234567`\n\n"
        "برای لغو: /cancel"
    )


@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def flow(event):
    uid = event.sender_id
    if not is_owner(event):
        return
    if uid not in state:
        return
    text = (event.raw_text or "").strip()
    if text.startswith("/"):
        return

    st = state[uid]

    # --- مرحله ۱: شماره ---
    if st["step"] == "phone":
        phone = re.sub(r"[^\d+]", "", text)
        if not phone.startswith("+") or len(phone) < 8:
            return await event.respond("فرمت شماره درست نیست. مثال: `+989121234567`")

        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except FloodWaitError as e:
            await client.disconnect()
            await reset(uid)
            return await event.respond(f"⏳ محدودیت تلگرام. {e.seconds} ثانیه صبر کن.")
        except Exception as e:
            await client.disconnect()
            await reset(uid)
            return await event.respond(f"خطا در ارسال کد: `{e}`")

        st.update(
            client=client,
            phone=phone,
            hash=sent.phone_code_hash,
            step="code",
        )
        await event.respond(
            "✅ کد ارسال شد.\n\n"
            "کد را **با فاصله یا خط تیره بین ارقام** بفرست تا تلگرام آن را باطل نکند.\n"
            "مثال: اگر کد `12345` است، بفرست: `1 2 3 4 5`\n\n"
            "برای لغو: /cancel"
        )
        return

    # --- مرحله ۲: کد ---
    if st["step"] == "code":
        code = re.sub(r"\D", "", text)
        if not code:
            return await event.respond("کد نامعتبر است.")
        client = st["client"]
        try:
            await client.sign_in(
                phone=st["phone"], code=code, phone_code_hash=st["hash"]
            )
        except SessionPasswordNeededError:
            st["step"] = "2fa"
            return await event.respond("🔒 رمز دو مرحله‌ای (2FA) را بفرست.")
        except PhoneCodeInvalidError:
            return await event.respond("❌ کد اشتباه است. دوباره بفرست.")
        except PhoneCodeExpiredError:
            await reset(uid)
            return await event.respond("⌛ کد منقضی شد. دوباره /login بزن.")
        except Exception as e:
            await reset(uid)
            return await event.respond(f"خطا: `{e}`")

        return await finish(event, uid)

    # --- مرحله ۳: رمز دو مرحله‌ای ---
    if st["step"] == "2fa":
        client = st["client"]
        try:
            await client.sign_in(password=text)
        except PasswordHashInvalidError:
            return await event.respond("❌ رمز اشتباه است. دوباره بفرست.")
        except Exception as e:
            await reset(uid)
            return await event.respond(f"خطا: `{e}`")

        return await finish(event, uid)


async def finish(event, uid):
    st = state[uid]
    client = st["client"]
    me = await client.get_me()
    session_str = client.session.save()

    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        f.write(session_str)

    # در Render فایل‌سیستم ephemeral است؛ فایل با دیپلوی بعدی پاک می‌شود.
    # سشن را در لاگ هم چاپ می‌کنیم تا بتوانی در Environment Variables
    # به‌عنوان SESSION_STR بگذاری.
    print("\n" + "=" * 70, flush=True)
    print("SESSION_STR (در Render این را به‌عنوان متغیر محیطی بگذار):", flush=True)
    print(session_str, flush=True)
    print("=" * 70 + "\n", flush=True)

    await client.disconnect()
    state.pop(uid, None)

    # پیام حاوی سشن را بعد از ۶۰ ثانیه پاک می‌کنیم
    msg = await event.respond(
        f"✅ ورود موفق!\n\n"
        f"👤 {me.first_name} (`{me.id}`)\n"
        f"📁 سشن در `data/session.txt` ذخیره شد.\n"
        f"📋 همین سشن در لاگ سرویس هم چاپ شد (برای `SESSION_STR`).\n\n"
        f"حالا `python userbot.py` را اجرا کن.\n\n"
        f"⚠️ این پیام تا ۶۰ ثانیه دیگر حذف می‌شود."
    )
    await asyncio.sleep(60)
    try:
        await msg.delete()
    except Exception:
        pass


def main():
    print("Login bot is running...")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()


if __name__ == "__main__":
    main()
