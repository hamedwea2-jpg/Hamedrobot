#!/usr/bin/env bash
# supervisor.sh — ربات را نگه می‌دارد و همه‌چیز را در لاگ فایل می‌نویسد.
# لاگ: data/run.log   (بین ری‌استارت‌های سندباکس هم می‌ماند)
#
# اجرا:  nohup bash supervisor.sh >/dev/null 2>&1 &
set -u
cd "$(dirname "$0")"
mkdir -p data

LOG=data/run.log
echo "===============================================" >> "$LOG"
echo "[supervisor] start $(date -u '+%Y-%m-%d %H:%M:%S') UTC" >> "$LOG"

# اگر telethon نبود نصبش کن (سندباکس ممکن است ریست شده باشد)
python3 -c "import telethon" 2>/dev/null || {
    echo "[supervisor] installing telethon..." >> "$LOG"
    pip3 install --quiet "telethon>=1.36.0" >> "$LOG" 2>&1
}

N=0
while true; do
    N=$((N+1))
    echo "[supervisor] run #$N at $(date -u '+%H:%M:%S')" >> "$LOG"
    python3 -u run.py >> "$LOG" 2>&1
    RC=$?
    echo "[supervisor] exit code $RC at $(date -u '+%H:%M:%S')" >> "$LOG"
    # اگر سشن ساخته شده و یوزربات بالا بوده، یعنی کار تمام است؛ باز هم ری‌استارت می‌کنیم
    sleep 5
done
