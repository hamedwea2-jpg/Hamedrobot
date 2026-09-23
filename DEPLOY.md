# استقرار — راهنمای دقیق

## الف) روی سرور خودت (پیشنهادی — دائمی)

```bash
# ۱) فایل‌ها را ببر روی سرور
scp -r hc-userbot root@YOUR_SERVER:/opt/hc-userbot

# ۲) نصب خودکار
ssh root@YOUR_SERVER
cd /opt/hc-userbot && bash deploy.sh

# ۳) config.json را پر کن
nano /opt/hc-userbot/config.json
#    api_id / api_hash      <- https://my.telegram.org
#    login_bot_token        <- @BotFather
#    owner_id               <- @userinfobot

# ۴) ربات لاگین را ری‌استارت کن
systemctl restart loginbot

# ۵) در تلگرام: @ربات_لاگین -> /start -> /login -> شماره -> کد (با فاصله: 1 2 3 4 5)

# ۶) بعد از ساخت سشن، یوزربات را روشن کن
systemctl enable --now userbot

# ۷) لاگ‌ها
journalctl -u userbot -f
```

---

## ب) روی پنل هاست (فقط یک Startup Command)

| فیلد | مقدار |
|---|---|
| Startup Command | `python3 run.py` |
| Install command | `pip install -r requirements.txt` |
| Python | 3.10 یا بالاتر |
| Working directory | پوشه پروژه |

`run.py` هر دو را با هم بالا می‌آورد. اگر سشن نباشد منتظر می‌ماند و به محض
`/login` زدن، یوزربات **خودکار** استارت می‌شود.

⚠️ اگر پنل دیسک موقت دارد (مثل Render/Railway رایگان)، با هر دیپلوی
`data/session.txt` پاک می‌شود و باید دوباره `/login` بزنی. برای دیسک دائم
volume وصل کن یا روی VPS ببر.

---

## ج) اجرای محلی (تست)

```bash
pip install -r requirements.txt
python3 run.py
```

---

## د) تست‌ها (بدون سشن)

```bash
python3 tests/selftest.py       # ۴۵ تست رفتاری روی کد واقعی userbot.py
python3 tests/test_loginbot.py  # ۲۲ تست جریان ورود
python3 tests/check_py310.py    # سازگاری با Python 3.10/3.11
```

این تست‌ها کلاینت تلگرام را شبیه‌سازی می‌کنند ولی **خودِ هندلرهای
`userbot.py` و `login_bot.py`** را اجرا می‌کنند — نه یک نسخه بازنویسی‌شده.

---

## ه) سوپروایزر ساده (اگر systemd نداری)

```bash
nohup bash supervisor.sh >/dev/null 2>&1 &
tail -f data/run.log
```

`supervisor.sh` ربات را نگه می‌دارد، در صورت کرش ری‌استارت می‌کند و لاگ را
در `data/run.log` می‌نویسد.

---

## و) اولین کارها بعد از بالا آمدن

```
.doctor        <- سلامت: تاخیر ایونت‌لوپ، صف، آمار
.stats         <- آمار
.pmtest        <- وضعیت پاسخ خودکار پی‌وی
.pm on         <- روشن کردن پاسخ پی‌وی
.pmadmins on   <- (اختیاری) خودت هم جواب بگیری
.addchat       <- داخل هر گروه خودت بزن
```

---

## ز) امنیت — این را جدی بگیر

1. **توکن ربات لاگین لو رفته.** لینک دانلود عمومی بود. در @BotFather:
   `/mybots` → ربات → API Token → **Revoke**.
2. **`api_hash` لو رفته.** در https://my.telegram.org یکی جدید بساز.
3. `data/session.txt` = دسترسی کامل به اکانت. هرگز share نکن.
   روی سرور: `chmod 600 data/session.txt`
4. `data/` را در `.gitignore` نگه دار (هست).

---

## ح) ظرفیت واقعی

با فاصله ۲۰–۳۰ ثانیه:

```
۸۶۴۰۰ ÷ ۲۵ ≈ ۳۵۰۰ ری‌اکشن در روز
```

اگر ورودی گروه‌هایت بیشتر است، یا `.delay 8 15` بگذار یا گروه‌های فعال را
کمتر کن. `.delay` زیر ۳ ثانیه پذیرفته نمی‌شود (خطر بن).
