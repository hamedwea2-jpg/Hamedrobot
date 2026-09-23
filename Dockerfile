# ---------------------------------------------------------------
# Dockerfile — HeavenCloud Userbot روی Render
# ---------------------------------------------------------------
# نکته: cryptg از سورس کامپایل می‌شود، پس build-essential لازم است.
# مرحلهٔ دوم (slim) باعث می‌شود ایمیج نهایی کوچک بماند.

FROM python:3.11-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim

# تلگرام گاهی به وقت دقیق نیاز دارد؛ بدون آن TLS handshake ممکن است رد شود
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local

WORKDIR /app
COPY . .

# DATA_DIR را روی mount path دیسک می‌گذاریم تا state.db بین دیپلوی‌ها بماند
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/var/data

RUN mkdir -p /var/data

# Web Service باید روی پورتی که Render در متغیر PORT می‌دهد گوش بدهد،
# وگرنه دیپلوی «ناموفق» علامت می‌خورد. health.py همان کار run.py را
# می‌کند به‌علاوهٔ یک اندپوینت /healthz.
#
# اگر سرویس را از نوع Background Worker ساختی، این را عوض کن به:
#   CMD ["python3", "run.py"]
CMD ["python3", "health.py"]
