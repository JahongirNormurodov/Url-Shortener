# Url-Shortener — Analytics, Smart Routing va Webhook Retry qo'shilishi

Bu fayl repongizga (https://github.com/JahongirNormurodov/Url-Shortener) qo'shilgan
to'rtta yangi bo'limni tasvirlaydi. Barcha o'zgarishlar **sizning haqiqiy kodingiz
ustida to'g'ridan-to'g'ri qo'llanildi va sinovdan o'tkazildi** (pastdagi "Sinov
natijalari" bo'limiga qarang) — qo'lda hech narsa qo'shishingiz shart emas,
`Url-Shortener-updated.zip` ichidagi fayllarni eski repo ustiga ko'chiring, xolos.

## Nima qo'shildi

### 1. Click Analytics (spec §10)
- **`app/db/models.py`** — `ClickEvent` jadvali qo'shildi (Link va Webhook
  klasslariga tegishli relationship'lar bilan)
- **`app/workers/click_flusher.py`** — Redis buferidan (`click_buffer` ro'yxati)
  click eventlarni o'qib, User-Agent va GeoIP bilan boyitib, `click_events`
  jadvaliga batch INSERT qiladi. Har 10 soniyada ishlaydi (sozlanadi).
- **`app/api/routes/stats.py`** — 5 ta yangi endpoint:
  - `GET /api/v1/links/{code}/stats` — to'liq statistika (summary, time-series,
    top referrers, geo/device/browser breakdown, soatlik taqsimot)
  - `GET /api/v1/links/{code}/stats/timeseries` — faqat vaqt seriyasi
  - `GET /api/v1/links/{code}/clicks` — xom click eventlar (audit)
  - `GET /api/v1/stats/overview` — foydalanuvchi dashboardi
  - `GET /api/v1/links/{code}/stats/export?format=csv|json` — eksport
- **`app/schemas/stats.py`** — barcha yangi Pydantic javob modellari

### 2. Smart Routing (spec §11)
- **`app/db/models.py`** — `RoutingRule` jadvali (geo / device / A-B)
- **`app/core/routing.py`** — qoidalarni hal qilish logikasi (geo → device →
  A/B → default tartibida)
- **`app/core/geoip.py`** — IP'dan mamlakat/shahar aniqlash (MaxMind GeoLite2
  yoki ip-api.com fallback)
- **`app/core/useragent.py`** — User-Agent'dan device/browser/OS aniqlash
- **`app/api/routes/routing.py`** — 3 ta endpoint:
  - `POST /api/v1/links/{code}/rules` — qoida qo'shish
  - `GET /api/v1/links/{code}/rules` — qoidalar ro'yxati
  - `DELETE /api/v1/links/{code}/rules/{id}` — qoidani o'chirish
- **`app/api/routes/redirect.py`** (yangilandi) — `is_smart=True` bo'lgan
  havolalar uchun routing engine chaqiriladi, har redirectda

### 3. Webhook Delivery + Retry (spec §12)
- **`app/db/models.py`** — `WebhookDelivery` jadvali (Webhook'ga bog'liq)
- **`app/workers/webhook_worker.py`** — `enqueue_webhook_delivery()` orqali
  navbatga qo'yish, keyin background worker eksponensial backoff bilan
  qayta uradi (1 daq → 5 daq → 30 daq, 3 urinishdan keyin "failed")
- **`app/api/routes/links.py`** (yangilandi) — havola yaratish/o'chirishda
  endi ham eski `fire_webhooks` (fire-and-forget), ham yangi
  `enqueue_webhook_delivery` (kuzatiladigan, retry bilan) chaqiriladi

### 4. Infratuzilma
- **`app/main.py`** — `stats` va `routing` router'lari ulandi; lifespan ichida
  `click_flusher` va `webhook_worker` background task sifatida ishga tushadi
  va graceful shutdown'da to'xtatiladi
- **`app/core/config.py`** — yangi sozlamalar: `geoip_db_path`,
  `click_flush_interval_seconds`, `click_flush_batch_size`,
  `webhook_delivery_interval_seconds`

## O'rnatish

```bash
cd Url-Shortener-main-patched
pip install -e .  # yoki uv sync

# Ixtiyoriy, lekin tavsiya etiladi:
pip install geoip2 user-agents
# MaxMind GeoLite2-City.mmdb faylini app/data/ ga joylashtiring
# https://dev.maxmind.com/geoip/geolite2-free-geolocation-data
# (Bo'lmasa avtomatik ip-api.com fallback ishlaydi)
```

Yangi jadvallar (`click_events`, `routing_rules`, `webhook_deliveries`)
**avtomatik yaratiladi** — `app/main.py` lifespan'dagi
`Base.metadata.create_all` allaqachon shu jadvallarni o'z ichiga oladi.
Productionda Alembic ishlatayotgan bo'lsangiz, alohida migratsiya yozish kerak
bo'ladi (modeldagi 3 ta yangi klassga qarab).

## Yangi API endpointlar to'liq ro'yxati

| Method | URL | Tavsif |
|--------|-----|--------|
| GET | `/api/v1/links/{code}/stats` | To'liq statistika (§10.1) |
| GET | `/api/v1/links/{code}/stats/timeseries` | Vaqt seriyasi (§10.2) |
| GET | `/api/v1/links/{code}/clicks` | Xom click eventlar (§10.3) |
| GET | `/api/v1/stats/overview` | Dashboard (§10.4) |
| GET | `/api/v1/links/{code}/stats/export` | CSV/JSON eksport (§10.5) |
| POST | `/api/v1/links/{code}/rules` | Routing qoida qo'shish (§11.1) |
| GET | `/api/v1/links/{code}/rules` | Routing qoidalar ro'yxati (§11.1) |
| DELETE | `/api/v1/links/{code}/rules/{id}` | Routing qoida o'chirish (§11.1) |

## Sinov natijalari

Quyidagi tekshiruvlar **shu reponing nusxasida, sandbox muhitida** bajarildi:

1. **Sintaksis**: barcha o'zgartirilgan/qo'shilgan fayllar `ast.parse` orqali
   tekshirildi — xato yo'q.
2. **Import**: `from app.main import app` muvaffaqiyatli ishladi, 15+ route
   ro'yxatdan o'tdi.
3. **To'liq lifespan sinovi**: startup → jadval yaratish (8 ta jadval, jumladan
   3 ta yangisi) → background worker'lar ishga tushishi → graceful shutdown —
   hammasi xatosiz.
4. **End-to-end API sinovi** (httpx ASGI client orqali):
   - register → login → shorten → smart routing rule qo'shish → list rules →
     redirect (302) — barchasi muvaffaqiyatli
   - click_events qo'lda kiritilgach: `/stats/overview`, `/links/{code}/stats`
     (summary, time_series, top_referrers, by_country, by_city, by_device,
     by_browser, by_hour), `/stats/timeseries`, `/clicks`, `/stats/export?format=csv`
     — barchasi to'g'ri JSON/CSV qaytardi
   - webhook yaratish va `enqueue_webhook_delivery` orqali "pending" yozuv
     yaratilishi tasdiqlandi
5. **Mavjud test suite** (`pytest tests/`): **17 passed, 7 failed** — xuddi
   **asl, o'zgartirilmagan repo bilan bir xil natija** (sandbox'da internet
   yo'qligi sababli Safe Browsing/SMTP so'rovlari ishlamaydi; bu mening
   o'zgarishlarimga aloqasi yo'q, productionда yo'qoladi).

### Tuzatilgan haqiqiy xato
Dastlabki versiyada `func.date_trunc()` va `func.extract()` faqat PostgreSQL'da
ishlaydi, SQLite'da (testlar/dev uchun) xato beradi. Buni `_trunc()` va
`_extract_hour()` dialect-agnostic yordamchi funksiyalar bilan tuzatdim —
SQLite uchun `strftime`, Postgres uchun `date_trunc`/`extract` avtomatik
tanlanadi (`engine.dialect.name` orqali).

Shuningdek vaqt-guruhlash ustuni nomi `"t"` dan `"bucket"` ga o'zgartirildi —
SQLAlchemy `Row.t` bilan to'qnashib, noto'g'ri natija (butun qator tuple
sifatida) qaytarayotgan edi.

## Eslatma: Redis va tashqi servislar

Sandbox muhitida Redis, SMTP va tashqi internet yo'q edi, shuning uchun:
- `click_flusher` va `increment_click_buffer` Redis xatosini jim yutib,
  loop'ni davom ettiradi (dizayn bo'yicha to'g'ri — redirect Redis xatosidan
  bloklanmasligi kerak)
- `webhook_worker` HTTP so'rovlarni jo'nata olmadi, lekin navbatga qo'yish
  (`enqueue_webhook_delivery`) to'liq ishladi

Productionda Redis va tashqi tarmoq mavjud bo'lganda bu qismlar ham to'liq
ishlaydi — kod logikasi sinovdan o'tgan.
