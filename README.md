# SMS Gateway

USB modem (AT buyruqlar) orqali SMS yuboruvchi shaxsiy API xizmati. **Python · FastAPI · SQLite · Docker.**
`sms.elshod.date` (Cloudflare Tunnel) orqali loyihalaringiz (KinderDesk, clinic va boshqalar) SMS yuboradi. Savdo uchun emas.

**Imkoniyatlari**

- Modemlarni avtomatik topish (hot-plug), bir nechta modem, navbat bilan (FIFO) yuborish
- Har loyiha uchun alohida API kalit (`Authorization: Bearer ...`), kunlik/oylik limit va statistika
- Modem uchun oylik SMS paketi limiti + **USSD orqali qoldiqni avtomatik o'qish**
- Har SMS jurnali (CSV eksport), OTP raqamlarini jurnalda yashirish
- Webhook (HMAC imzo bilan) va `GET /sms/{id}` orqali natijani olish
- Admin panel (o'zbekcha, yorug'/qorong'i): dashboard, tizim holati, loyihalar, modemlar, jurnal, test SMS, sozlamalar, AT-konsol
- Modemsiz sinash uchun **mock rejimi**

---

## 1. Kompyuterda (Windows) sinash, modemsiz

```bat
cd C:\PythonLoyhalar\SMSGetway
run_dev.bat
```

`run_dev.bat` virtual muhit yaratadi, kutubxonalarni o'rnatadi, `.env` ni `MODEM_MODE=mock` bilan yaratadi va serverni ishga tushiradi.

- Admin panel: <http://127.0.0.1:8000/admin>, login `admin`, parol: `.env` dagi `ADMIN_PASSWORD` (`ozgartiring-12345`)
- API hujjati (Swagger): <http://127.0.0.1:8000/docs>
- Testlar: `.venv\Scripts\activate` → `pytest`

Panelda: **Loyihalar → Yangi loyiha** (API kalit bir marta ko'rsatiladi) → **Test SMS** yoki `curl` bilan yuboring. Mock rejimda SMS haqiqatda ketmaydi, faqat jarayon sinaladi.

## 2. Serverga o'rnatish (uydagi Ubuntu server, 192.168.100.38)

GitHub'ga yuklagach:

```bash
# 1) Docker bo'lmasa o'rnating (docker + compose plugin)
git clone https://github.com/<foydalanuvchi>/<repo>.git sms_gateway
cd sms_gateway
cp .env.example .env
nano .env          # ADMIN_PASSWORD ni o'zgartiring, MODEM_MODE=auto qoldiring

# 2) ModemManager (Ubuntu'da modemni band qilib qo'yadi) ni o'chiring
sudo systemctl disable --now ModemManager

# 3) Modemni ulang va portlarni tekshiring
lsusb
ls -l /dev/ttyUSB* /dev/ttyACM*

# 4) Ishga tushiring
docker compose up -d --build
docker compose logs -f app
```

Admin panel: `http://192.168.100.38:8000/admin`. Modem 15 soniya ichida avtomatik topiladi (**Modemlar** bo'limi). Topilmasa **🔄 Qayta skanerlash**.

> **Huawei (E3372 / E173 va h.k.) modemlar:** agar `lsusb` da `12d1:1f01` (CD-ROM rejimi) ko'rinsa, `sudo apt install usb-modeswitch` qilib modemni qayta ulang. Stick **HiLink (192.168.8.1)** rejimida bo'lsa AT port chiqmaydi, "Stick" (serial) rejimiga o'tkazish kerak (modem modelini yuborsangiz yo'riqnomasini beraman).

**Muhim:** `docker-compose.yml` dagi `/dev:/dev` va `device_cgroup_rules` modem ulanib-uzilganda konteynerni qayta ishga tushirmasdan topish imkonini beradi.

## 3. Cloudflare Tunnel (`sms.elshod.date`)

1. Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel** (Cloudflared) → token oling.
2. **Public hostname**: `sms.elshod.date` → Service: `HTTP` · `app:8000` (agar `cloudflared` shu compose ichida ishlasa) yoki `localhost:8000` (serverda alohida o'rnatilgan bo'lsa).
3. `.env` ga yozing: `TUNNEL_TOKEN=...`, `COOKIE_SECURE=true`, `ENABLE_DOCS=false`.
4. `docker compose --profile tunnel up -d`

Tavsiya: **Zero Trust → Access → Applications** orqali faqat `sms.elshod.date/admin*` yo'liga email-OTP himoyasini qo'shing. `/api/*` yo'liga **qo'shmang**, u o'z API kaliti bilan himoyalangan.

## 4. API

Base URL: `https://sms.elshod.date/api/v1` · Autentifikatsiya: `Authorization: Bearer <API_KALIT>` (yoki `X-API-Key`).

| Metod | Yo'l | Vazifa |
|---|---|---|
| POST | `/sms/send` | SMSni navbatga qo'yadi (`202`) |
| GET | `/sms/{id}` | Xabar holati |
| GET | `/sms?external_id=&status=&limit=` | Xabarlar ro'yxati |
| DELETE | `/sms/{id}` | Navbatdagi xabarni bekor qilish |
| GET | `/me/usage` | Loyiha statistikasi va limitlari |
| GET | `/health` | Ochiq holat (`ok` / `degraded` / `down`) |

```bash
curl -X POST https://sms.elshod.date/api/v1/sms/send \
  -H "Authorization: Bearer sk_sms_XXXX" -H "Content-Type: application/json" \
  -d '{"to": "+998901234567", "text": "Tasdiqlash kodi: 482910", "external_id": "otp-123"}'
```

```json
{ "id": 42, "to": "+998901234567", "status": "queued", "parts": 1, "encoding": "gsm7", "external_id": "otp-123", "duplicate": false }
```

- **Raqam:** `+998901234567`, `998901234567`, `901234567`, `90 123-45-67` kabi ko'rinishlar qabul qilinadi.
- **`external_id`** (ixtiyoriy): takror so'rovdan himoya. Bir xil `external_id` bilan qayta yuborsangiz, yangi SMS yaratilmaydi, mavjud xabar qaytadi (`duplicate: true`).
- **Holatlar:** `queued → sending → sent` | `failed` | `expired` (navbatda juda uzoq kutdi) | `cancelled`.
- **Xato formati:** `{"error": {"code": "...", "message": "..."}}`

| Kod | HTTP | Ma'nosi |
|---|---|---|
| `invalid_api_key` | 401 | Kalit noto'g'ri |
| `project_disabled` | 403 | Loyiha o'chirilgan |
| `invalid_phone`, `empty_text`, `text_too_long`, `validation_error` | 422 | So'rov noto'g'ri |
| `rate_limited` | 429 | Bir raqamga juda tez-tez (standart: 1/daqiqa, 5/soat) |
| `project_daily_limit`, `project_monthly_limit` | 429 | Loyiha limiti tugadi |
| `quota_exceeded` | 429 | Barcha modemlarda SMS paketi tugagan |
| `no_modem` | 503 | Yoqilgan modem yo'q |

### Webhook (natija xabari)

Loyiha sozlamalarida **Webhook URL** kiritilsa, xabar yakunlanganda (`sent`, `failed`, `expired`) shu manzilga `POST` yuboriladi. Javob `2xx` bo'lmasa 30 s, 2 min, 10 min, 1 soat, 6 soat oralig'ida qayta uriniladi.

```json
{ "event": "sms.status", "data": { "id": 42, "external_id": "otp-123", "to": "+998901234567", "status": "sent", "parts": 1, "parts_sent": 1, "error": null, "sent_at": "2026-11-07T10:15:03Z", "project": "KinderDesk" } }
```

Sarlavhalar: `X-SMS-Timestamp`, `X-SMS-Signature: sha256=HMAC_SHA256(secret, timestamp + "." + body)`. Tekshirish kodi `clients/` ichida (Laravel: `SmsGateway::verifyWebhook`, Python: `verify_webhook`). **Webhook shart emas**: OTP uchun odatda `external_id` + (kerak bo'lsa) `GET /sms/{id}` yetarli.

### Klientlar

- **Laravel:** `clients/laravel/SmsGateway.php` (sozlash yo'riqnomasi fayl boshida)
- **Python:** `clients/python/sms_gateway_client.py` (qo'shimcha kutubxonasiz)

## 5. Limitlar qanday ishlaydi

- **1 SMS = 1 qism.** Lotin matn (`o'`, `g'` oddiy apostrof bilan) 160 belgi; kirill yoki maxsus belgi bo'lsa 70 belgi. Uzun xabar qismlarga bo'linadi, **limit qismlar bo'yicha** hisoblanadi.
- **Apostrof avtomatik tuzatiladi:** `ʻ ’ ‘ ´` oddiy `'` ga almashtiriladi (SMS qisqa va arzon bo'ladi). Sozlamalarda o'chirish mumkin.
- **Modem paketi:** Modem sahifasida "Oylik SMS paketi" kiriting. Hisob har oyning 1-sanasida (Toshkent vaqti) noldan boshlanadi. Paket tugasa, shu modem orqali yuborilmaydi; boshqa modem bo'lsa xabar unga o'tadi.
- **Loyiha limiti:** har loyihaga ixtiyoriy kunlik/oylik limit.
- Navbatdagi xabar `Navbatda kutish muddati` (standart 5 daqiqa) ichida modem/limit topilmasa `expired` bo'ladi.

### USSD orqali paket qoldig'ini avtomatik o'qish

**Modemlar → modem → "Paket qoldig'ini avtomatik o'qish"** da operatorning USSD kodini kiriting (masalan paket/qoldiq so'rovi kodi; menyu bo'lsa vergul bilan javoblar: `*100#,1,2`). Tizim har 6 soatda (Sozlamalarda o'zgaradi) yuboradi va javobdan "… ta SMS" sonini topadi.

1. **📡 USSD ni hozir tekshirish** tugmasini bosing.
2. **Oxirgi USSD javobi** da operator matnini ko'rasiz. Son topilmasa, **Regex** maydoniga naqsh yozing (birinchi guruh = son), masalan `SMS:\s*(\d+)`.
3. Kod faqat modem SIMida ishlaydi, shuning uchun operator kodini telefonda avval sinab ko'ring.

Har yuborilgan SMS qoldiqni darhol kamaytiradi, USSD esa uni haqiqiy qiymatga tuzatadi.

## 6. Modem ulangach tekshirish ro'yxati

1. `lsusb` va `ls /dev/ttyUSB*`: modem va portlar ko'rinadimi?
2. Panel → **Modemlar**: modem "tayyor", operator va signal ko'rinadimi? (IMEI, operator nomi AT orqali o'qiladi.)
3. Modem sahifasidagi **AT konsol**: `AT+CGMM` (model), `AT+CPIN?` (`READY` bo'lishi kerak), `AT+CSQ`, `AT+COPS?`.
4. **Test SMS** → o'z raqamingizga yuboring. Jurnalda holat `Yuborildi`.
5. USSD kodini kiriting va qoldiq o'qilishini tekshiring.
6. Muammo bo'lsa menga yuboring: `lsusb`, `ls -l /dev/ttyUSB*`, `docker compose logs --tail=100 app`, AT konsol natijasi (`AT+CGMM`, `AT+CPIN?`).

Tez-tez uchraydigan sabablar: ModemManager o'chirilmagan; SIMda PIN so'raladi (`AT+CPIN?` → `SIM PIN`); Huawei modem CD-ROM/HiLink rejimida; USB port quvvati yetmayapti (USB-hub orqali ulang); konteyner `/dev` ni ko'rmayapti (`docker compose up -d --force-recreate`).

## 7. Loyiha tuzilmasi

```
app/
  main.py          FastAPI ilovasi, xato ishlovchilari
  api.py           /api/v1 (loyihalar uchun)
  admin.py         admin panel marshrutlari
  models.py        SQLite jadvallari
  runtime.py       fon xizmatlari (modem skaneri, dispatcher, webhook, tozalash)
  modem/           at_modem.py (AT/PDU/USSD), mock.py, pool.py (avto topish)
  services/        gsm.py (kodlash), messaging.py, dispatcher.py, webhook.py, stats.py, settings_store.py
  templates/, static/
clients/           Laravel va Python klientlari
tests/             pytest (GSM/PDU, soxta AT-modem, API, admin)
```

Ma'lumotlar `data/` papkasida (`sms_gateway.db`, `secret.key`). Zaxira nusxa uchun shu papkani nusxalash kifoya.

## 8. Ma'lum cheklovlar

- Kiruvchi SMS va yetkazib berish hisoboti (delivery report) yo'q. `sent` = modem operatorga qabul qilganini tasdiqladi.
- Bir vaqtda bitta SMS/modem (bir SMS ~2–10 soniya). Kuniga 20–30 SMS uchun ortig'i bilan yetarli.
- USSD javob formati operator va modemga qarab farq qiladi, shuning uchun birinchi ulashda regex moslash kerak bo'lishi mumkin.
- Server qayta ishga tushganda yuborilayotgan (`sending`) xabar natijasi noma'lum bo'lgani uchun `failed` deb belgilanadi (takror SMS yubormaslik uchun).
#   S M S G e t w a y E N D  
 