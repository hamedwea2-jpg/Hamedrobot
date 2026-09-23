# دیپلوی روی Render

## ⚠️ اول این را بخوان — سه محدودیت Render

| محدودیت | اثر روی ربات | راه‌حل |
|---|---|---|
| **فایل‌سیستم ephemeral** | `data/session.txt` با هر دیپلوی پاک می‌شود | سشن را در متغیر محیطی `SESSION_STR` بگذار |
| **Background Worker رایگان نیست** | حداقل ~$۷/ماه | یا پولی، یا همان پنل قبلی‌ات |
| **Web Service رایگان بعد از ۱۵ دقیقه بی‌ترافیک می‌خوابد** | ربات از تلگرام قطع می‌شود | برای یوزربات به درد نمی‌خورد |

**پیشنهاد من:** `Background Worker` + `Persistent Disk` (پولی). اگر نمی‌خواهی
پول بدهی، همان پنل قبلی (Koyeb) برایت بهتر است — چون ربات ۲۴ ساعته باید
بیدار بماند.

---

## مرحلهٔ ۱ — گرفتن سشن (یک بار، روی کامپیوتر خودت)

چون فایل‌سیستم Render پاک‌شدنی است، **سشن را نمی‌شود آن‌جا ساخت**. اول
محلی بگیر:

```bash
pip3 install -r requirements.txt
python3 login_bot.py
```

در تلگرام به ربات لاگین `/login` بزن، شماره و کد را بده. بعد:

```
/session
```

رشتهٔ سشن را می‌دهد (۹۰ ثانیه بعد پیام حذف می‌شود). همان را کپی کن.

> اگر سشن را از قبل داری، محتوای `data/session.txt` همان چیز است.

---

## مرحلهٔ ۲ — ریپو

این فایل‌ها را در ریشهٔ ریپو بگذار (همه هستند):

```
Dockerfile      ← ایمیج
render.yaml     ← Blueprint (worker + disk + env)
run.py          ← Startup Command برای worker
health.py       ← فقط اگر Web Service ساختی
requirements.txt
config.json     ← تنظیمات ربات
```

`.gitignore` را چک کن — `data/` باید ignore باشد تا `session.txt` و
`state.db` اشتباهی در ریپو نروند.

---

## مرحلهٔ ۳ — دیپلوی

### راه A: Blueprint (ساده‌ترین)

1. Render → **New +** → **Blueprint**
2. ریپو را وصل کن → `render.yaml` را می‌خواند و سرویس را می‌سازد
3. قبل از دیپلوی، در **Environment** سرویس اینها را پر کن:

```
API_ID          = <api_id>
API_HASH        = <api_hash>
LOGIN_BOT_TOKEN = <توکن ربات لاگین>
SESSION_STR     = <رشتهٔ سشن>
```

4. **Manual Deploy** بزن.

### راه B: دستی

1. **New +** → **Background Worker**
2. Runtime: **Docker**
3. Region: `frankfurt`
4. Plan: `Starter` (worker رایگان ندارد)
5. **Add Disk** → Mount path: `/var/data` → Size: `1 GB`
6. Environment Variables: همان چهار تای بالا
7. Deploy

---

## مرحلهٔ ۴ — بررسی

در **Logs** باید اینها را ببینی:

```
🚀 HeavenCloud Userbot — starting...
✅ Login bot running: @yourloginbot
✅ Userbot started as YourName (123456789)
   Groups: 0 | Delay: 20-30s
```

اگر دیدی:

```
⏳ سشن پیدا نشد. در تلگرام به ربات لاگین /login بزن...
```

یعنی `SESSION_STR` را درست نگذاشته‌ای.

---

## نکتهٔ مهم دربارهٔ `state.db`

`state.db` جدول «چه کسی ری‌اکشن گرفته» را نگه می‌دارد. اگر پاک شود، ربات
به همه **دوباره** ری‌اکشن می‌زند (کول‌داون‌ها از دست می‌روند).

- با **Persistent Disk** روی `/var/data` → می‌ماند ✅
- بدون دیسک → با هر دیپلوی از دست می‌رود ⚠️

`DATA_DIR=/var/data` در `render.yaml` گذاشته شده تا `state.db` روی دیسک
نوشته شود.

---

## اگر Web Service ساختی

به‌جای `run.py` از `health.py` استفاده کن:

```
Startup Command:  python3 health.py
Health Check Path: /healthz
```

`health.py` همان کار `run.py` را می‌کند + یک اندپوینت `/healthz` روی
`$PORT` که Render لازم دارد. ولی یادت باشد سرویس **رایگان** بعد از ۱۵
دقیقه بی‌ترافیک می‌خوابد و ربات قطع می‌شود.

---

## دستورهای مفید بعد از دیپلوی

همه را در تلگرام، در پی‌وی یوزربات می‌زنی:

```
.doctor       ← وضعیت کامل: صف، ظرفیت، اتصال، FloodWait
.flood        ← تاریخچهٔ FloodWait
.chats        ← گروه‌های فعال و اینکه ری‌اکشنشان باز است یا نه
.chatcheck    ← چک فوری همهٔ گروه‌ها
.window       ← پنجرهٔ کاری و سقف خودکار
.queue compact ← فشرده‌سازی صف
```

---

## عیب‌یابی

| علامت | علت | کار |
|---|---|---|
| `⏳ سشن پیدا نشد` | `SESSION_STR` خالی یا غلط | مرحلهٔ ۱ را دوباره |
| `ValueError: Not a valid string` | سشن ناقص کپی شده | دوباره از `/session` کپی کن |
| سرویس مدام ریستارت می‌شود | `api_id`/`api_hash` غلط | چک کن |
| `🔴 اتصال` در `.doctor` | شبکه یا محدودیت اکانت | `.flood` را ببین |
| ری‌اکشن نمی‌زند ولی صف پر است | گروه‌ها ری‌اکشنشان بسته | `.chatcheck` |

---

## هزینه

| گزینه | هزینهٔ ماهانه | مناسب؟ |
|---|---|---|
| Background Worker Starter + 1GB Disk | ~$۷ + $۰.۲۵ | ✅ بله |
| Web Service Free | $۰ | ❌ بعد از ۱۵ دقیقه می‌خوابد |
| Web Service Starter (بدون دیسک) | ~$۷ | ⚠️ `state.db` از دست می‌رود |
