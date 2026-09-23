"""
run.py — اجرای همزمان ربات لاگین و یوزربات در یک پروسه
--------------------------------------------------------
مناسب پنل‌هایی که فقط یک Startup Command می‌گیرند.

رفتار:
  • ربات لاگین همیشه بالا می‌آید (برای /login زدن)
  • اگر data/session.txt وجود داشته باشد، یوزربات هم بالا می‌آید
  • اگر سشن نباشد، منتظر می‌ماند و به محض ساخته شدن، یوزربات را خودکار استارت می‌کند
    (لازم نیست دستی ری‌استارت کنی)

Startup Command:  python3 run.py
"""

import asyncio
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE, "data", "session.txt")

sys.path.insert(0, BASE)


async def run_login_bot():
    """ربات لاگین را در همین ایونت‌لوپ اجرا می‌کند."""
    import login_bot as lb
    await lb.bot.start(bot_token=lb.BOT_TOKEN)
    me = await lb.bot.get_me()
    print(f"✅ Login bot running: @{me.username}", flush=True)
    await lb.bot.run_until_disconnected()


def _env_session():
    """سشن از متغیر محیطی (راه اصلی در Render، چون فایل پاک می‌شود)."""
    return (os.environ.get("SESSION_STR") or "").strip()


def _session_is_valid():
    """سشن موجود و قابل پارس است؟ از محیط یا از فایل."""
    env = _env_session()
    if env:
        try:
            from telethon.sessions import StringSession
            StringSession(env)
            return True
        except Exception:
            print("⚠️ SESSION_STR معتبر نیست.", flush=True)
            return False
    if not os.path.exists(SESSION_FILE):
        return False
    try:
        from telethon.sessions import StringSession
        txt = open(SESSION_FILE, encoding="utf-8").read().strip()
        if not txt:
            return False
        StringSession(txt)          # اگر خراب باشد استثنا می‌دهد
        return True
    except Exception:
        return False


async def wait_for_session():
    """تا ساخته شدن یک سشن معتبر صبر می‌کند."""
    warned_missing = warned_bad = False
    while True:
        if _session_is_valid():
            await asyncio.sleep(2)   # اطمینان از کامل شدن نوشتن فایل
            print("✅ سشن معتبر پیدا شد. یوزربات در حال استارت...", flush=True)
            return
        if os.path.exists(SESSION_FILE):
            if not warned_bad:
                print("⚠️ سشن خراب است. /logout بزن و دوباره /login کن.", flush=True)
                warned_bad = True
        elif not warned_missing:
            print("⏳ سشن پیدا نشد. در تلگرام به ربات لاگین /login بزن...", flush=True)
            print("   (در Render می‌توانی SESSION_STR را هم در Environment بگذاری)",
                  flush=True)
            warned_missing = True
        await asyncio.sleep(3)


async def run_userbot():
    await wait_for_session()

    # اگر سشن از محیط آمده ولی userbot.py موقع import فایل را ندیده،
    # مقدار را به او بده. وگرنه StringSession خالی می‌سازد و start()
    # دوباره شماره و کد می‌خواهد.
    env = _env_session()
    import userbot as ub
    if env and not ub.SESSION_STR:
        from telethon.sessions import StringSession
        ub.SESSION_STR = env
        ub.client = ub.TelegramClient(StringSession(env), ub.API_ID, ub.API_HASH)
        print("ℹ️ کلاینت با SESSION_STR محیطی ساخته شد.", flush=True)

    await ub.client.start()
    me = await ub.client.get_me()

    if not ub.OWNER_ID:
        ub.OWNER_ID = me.id
        ub.CFG.d["owner_id"] = me.id
        ub.CFG.save()

    print(f"✅ Userbot started as {me.first_name} ({me.id})", flush=True)
    print(f"   Groups: {len(ub.CFG['allowed_chats'])} | "
          f"Delay: {ub.CFG['reaction']['min_delay']}-"
          f"{ub.CFG['reaction']['max_delay']}s", flush=True)

    ub.sync_workers()
    asyncio.create_task(ub.janitor())
    await ub.client.run_until_disconnected()


async def safe_userbot():
    """اگر یوزربات خطا داد، ربات لاگین نباید بمیرد."""
    while True:
        try:
            await run_userbot()
        except Exception as e:
            print(f"❌ خطای یوزربات: {e}", flush=True)
            print("   ۱۵ ثانیه دیگر دوباره تلاش می‌کنم...", flush=True)
            await asyncio.sleep(15)
        else:
            return


async def main():
    print("🚀 HeavenCloud Userbot — starting...", flush=True)
    await asyncio.gather(
        run_login_bot(),
        safe_userbot(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 bye", flush=True)
