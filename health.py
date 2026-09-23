"""
health.py — اجرای ربات + یک اندپوینت سلامتی روی $PORT
------------------------------------------------------
فقط وقتی لازم است که سرویس را در Render از نوع **Web Service** بسازی.
Web Service باید روی پورتی که Render در متغیر `PORT` می‌دهد گوش بدهد،
وگرنه دیپلوی را «ناموفق» علامت می‌زند.

Background Worker به این فایل نیاز ندارد — همان `python3 run.py` کافی است.

Startup Command:  python3 health.py
"""

import asyncio
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import run as bot_run  # noqa: E402  — همان منطقی که run.py دارد

PORT = int(os.environ.get("PORT", "10000"))


async def health_server():
    """یک سرور HTTP خیلی کوچک، فقط برای اینکه Render سرویس را سالم بداند.

    عمدداً از کتابخانهٔ استاندارد است تا وابستگی تازه‌ای اضافه نشود.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/healthz", "/", "/health"):
                body = b"ok\n"
                self.send_response(200)
            else:
                body = b"not found\n"
                self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):     # لاگ هر درخواست را نمی‌خواهیم
            pass

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"🩺 health endpoint on 0.0.0.0:{PORT}/healthz", flush=True)
    while True:
        await asyncio.sleep(3600)


async def main():
    print(f"🚀 HeavenCloud Userbot (web mode, PORT={PORT})", flush=True)
    await asyncio.gather(
        health_server(),
        bot_run.main(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 bye", flush=True)
