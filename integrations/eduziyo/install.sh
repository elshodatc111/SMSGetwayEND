#!/usr/bin/env bash
# eduziyo (Laravel) ni SMS Gateway'ga ulaydi. Xavfsiz: o'zgargan fayllarning nusxasi saqlanadi, qayta ishga tushirsa ham zarar yo'q.
# Ishlatish:  bash ~/progects/SMSGetway/integrations/eduziyo/install.sh [~/progects/eduziyo]
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="${1:-$HOME/progects/eduziyo}"

[ -f "$DST/artisan" ] || { echo "XATO: Laravel loyiha topilmadi: $DST"; exit 1; }
for f in app/Services/SmsService.php config/services.php routes/console.php; do
  [ -f "$DST/$f" ] || { echo "XATO: $DST/$f yo'q"; exit 1; }
done

BK="$DST/.gateway-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BK/app/Services" "$BK/config" "$BK/routes"
cp -p "$DST/app/Services/SmsService.php" "$BK/app/Services/"
cp -p "$DST/config/services.php" "$BK/config/"
cp -p "$DST/routes/console.php" "$BK/routes/"
[ -f "$DST/.env" ] && cp -p "$DST/.env" "$BK/.env"

# 1) yangi fayllar
mkdir -p "$DST/app/Services/Sms" "$DST/app/Console/Commands"
cp -f "$SRC/app/Services/Sms/GatewayDriver.php" "$DST/app/Services/Sms/GatewayDriver.php"
cp -f "$SRC/app/Console/Commands/SmsGatewaySync.php" "$DST/app/Console/Commands/SmsGatewaySync.php"

# 2) mavjud fayllarga kichik qo'shimchalar
python3 - "$DST" <<'PY'
import sys, pathlib

root = pathlib.Path(sys.argv[1])


def patch(rel, marker, old, new):
    p = root / rel
    s = p.read_text(encoding="utf-8")
    if marker in s:
        print(f"  = {rel}: allaqachon bor")
        return
    if s.count(old) != 1:
        sys.exit(f"XATO: {rel} da kutilgan joy topilmadi (fayl o'zgargan?). Hech narsa buzilmadi, nusxa saqlangan.")
    p.write_text(s.replace(old, new), encoding="utf-8")
    print(f"  + {rel}")


patch(
    "app/Services/SmsService.php",
    "GatewayDriver",
    r"""        // Kanal: USB modem tirik bo'lsa shu, bo'lmasa Eskiz
        $modem = app(\App\Services\Sms\ModemDriver::class);""",
    r"""        // Kanal 1: SMS Gateway (sms.elshod.date, USB modemlar shu yerda). Sozlanmagan bo'lsa pastdagi eski yo'l ishlaydi.
        $gateway = app(\App\Services\Sms\GatewayDriver::class);
        if ($gateway->available()) {
            $gateway->send($sms);

            return;
        }

        // Kanal: USB modem tirik bo'lsa shu, bo'lmasa Eskiz
        $modem = app(\App\Services\Sms\ModemDriver::class);""",
)

patch(
    "config/services.php",
    "sms_gateway",
    """        'from' => env('ESKIZ_FROM', '4546'),
    ],
""",
    """        'from' => env('ESKIZ_FROM', '4546'),
    ],

    // SMS Gateway (sms.elshod.date): USB modemlar shu xizmat orqali. Kalit gateway admin panelidagi loyihadan olinadi.
    'sms_gateway' => [
        'url' => env('SMS_GATEWAY_URL'),
        'key' => env('SMS_GATEWAY_KEY'),
    ],
""",
)

patch(
    "routes/console.php",
    "sms:gateway-sync",
    "Schedule::command('sms:dispatch')->everyMinute()->withoutOverlapping();",
    "Schedule::command('sms:dispatch')->everyMinute()->withoutOverlapping();\n"
    "Schedule::command('sms:gateway-sync')->everyMinute()->withoutOverlapping();",
)

env = root / ".env"
if env.exists():
    s = env.read_text(encoding="utf-8")
    if "SMS_GATEWAY_URL" not in s:
        if not s.endswith("\n"):
            s += "\n"
        s += "\n# SMS Gateway (USB modemlar). Kalit bo'sh bo'lsa gateway o'chiq.\nSMS_GATEWAY_URL=http://192.168.100.38:7006\nSMS_GATEWAY_KEY=\n"
        env.write_text(s, encoding="utf-8")
        print("  + .env: SMS_GATEWAY_URL / SMS_GATEWAY_KEY qo'shildi (KEY ni o'zingiz kiriting)")
    else:
        print("  = .env: allaqachon bor")
PY

echo
echo "Tayyor. Zaxira nusxa: $BK"
echo "Keyingi qadamlar:"
echo "  1) SMS Gateway panelida 'Eduziyo' loyihasini yarating va API kalitni oling"
echo "  2) $DST/.env ichida SMS_GATEWAY_KEY=... qiling"
echo "  3) eduziyo'ni odatdagidek qayta yig'ing (deploy/deploy.sh)"
