#!/usr/bin/env bash
# نصب روی سرور heavencloud.in
# اجرا با root:  bash deploy.sh
set -e

APP=/opt/hc-userbot

echo ">> نصب پیش‌نیازها"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip

echo ">> کپی فایل‌ها به $APP"
mkdir -p $APP
cp -r ./* $APP/
cd $APP

echo ">> ساخت محیط مجازی"
python3 -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

echo ">> نصب سرویس‌ها"
cp loginbot.service /etc/systemd/system/
cp userbot.service  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now loginbot

cat <<'EOF'

✅ نصب تمام شد.

مرحله بعد:
  1) config.json را پر کن:  nano /opt/hc-userbot/config.json
       api_id, api_hash        -> از my.telegram.org
       login_bot_token         -> از @BotFather
       owner_id                -> آیدی عددی خودت (از @userinfobot)
  2) systemctl restart loginbot
  3) در تلگرام به ربات لاگین /start بعد /login بزن و شماره و کد را بفرست
  4) بعد از ساخت سشن:
       systemctl enable --now userbot
  5) لاگ‌ها:
       journalctl -u userbot -f
       tail -f /var/log/hc-userbot.log

داخل هر گروه خودت `.addchat` بزن تا فعال شود.
EOF
