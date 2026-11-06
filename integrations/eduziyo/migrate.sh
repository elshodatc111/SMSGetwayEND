#!/usr/bin/env bash
# USB modemni eduziyo'dan SMS Gateway'ga BITTA buyruq bilan o'tkazadi.
#
#   bash ~/progects/SMSGetway/integrations/eduziyo/migrate.sh        # so'raydi
#   bash ~/progects/SMSGetway/integrations/eduziyo/migrate.sh -y     # so'ramaydi
#
# Qadamlar: gateway'da "Eduziyo" loyihasi va API kalit -> eduziyo kodiga GatewayDriver -> eduziyo_modem to'xtatiladi
# -> gateway modemni oladi -> (xohlasangiz) eduziyo qayta yig'iladi. Zaxira nusxalar eduziyo/.gateway-backup-* da.
set -euo pipefail

GW="${GW:-$HOME/progects/SMSGetway}"
EDU="${EDU:-$HOME/progects/eduziyo}"
HOST="${GW_HOST:-192.168.100.38}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=0; [ "${1:-}" = "-y" ] && YES=1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mXATO: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$GW/docker-compose.yml" ] || die "Gateway papkasi topilmadi: $GW"
[ -f "$EDU/artisan" ] || die "eduziyo papkasi topilmadi: $EDU"
docker ps --format '{{.Names}}' | grep -qx sms_gateway || die "sms_gateway konteyneri ishlamayapti (cd $GW && docker compose up -d --build app)"

PORT="$(grep -E '^HTTP_PORT=' "$GW/.env" 2>/dev/null | tail -1 | cut -d= -f2 || true)"; PORT="${PORT:-8000}"

cat <<EOF
Bu skript:
  - gateway'da 'Eduziyo' loyihasini yaratadi (bor bo'lsa API kalitini yangilaydi)
  - gateway'da bir raqamga cheklovni o'chiradi va navbat muddatini 1 soatga qo'yadi
  - eduziyo kodiga GatewayDriver qo'shadi, .env ni yangilaydi
  - eduziyo_modem konteynerini TO'XTATIB O'CHIRADI (modem gateway'ga o'tadi)
EOF
if [ "$YES" -ne 1 ]; then
  read -r -p "Davom etilsinmi? [y/N] " a; [ "$a" = "y" ] || [ "$a" = "Y" ] || { echo "Bekor qilindi."; exit 0; }
fi

say "1/5 Gateway: Eduziyo loyihasi va sozlamalar"
OUT="$(docker exec sms_gateway python -m app.cli create-project Eduziyo)"
echo "$OUT" | grep -v '^KEY='
KEY="$(echo "$OUT" | sed -n 's/^KEY=//p' | tail -1)"
[ -n "$KEY" ] || die "API kalit olinmadi"
docker exec sms_gateway python -m app.cli set-setting rate_per_minute 0
docker exec sms_gateway python -m app.cli set-setting rate_per_hour 0
docker exec sms_gateway python -m app.cli set-setting message_ttl_seconds 3600

say "2/5 eduziyo kodiga GatewayDriver qo'shish"
bash "$HERE/install.sh" "$EDU"
sed -i "s|^SMS_GATEWAY_URL=.*|SMS_GATEWAY_URL=http://$HOST:$PORT|; s|^SMS_GATEWAY_KEY=.*|SMS_GATEWAY_KEY=$KEY|" "$EDU/.env"
grep -q '^MODEM_ENABLED=' "$EDU/.env" && sed -i 's/^MODEM_ENABLED=.*/MODEM_ENABLED=0/' "$EDU/.env" || echo 'MODEM_ENABLED=0' >> "$EDU/.env"
echo "  eduziyo/.env: SMS_GATEWAY_URL=http://$HOST:$PORT, SMS_GATEWAY_KEY=(yozildi), MODEM_ENABLED=0"

say "3/5 eduziyo_modem to'xtatilmoqda (modem portlari bo'shaydi)"
if docker ps -a --format '{{.Names}}' | grep -qx eduziyo_modem; then
  docker stop eduziyo_modem >/dev/null && docker rm eduziyo_modem >/dev/null && echo "  eduziyo_modem o'chirildi"
else
  echo "  eduziyo_modem yo'q (o'tkazib yuborildi)"
fi

say "4/5 Gateway modemni oladi"
sed -i '/^SERIAL_EXCLUDE=/d;/^MODEM_MODE=/d;/^MOCK_/d' "$GW/.env"
(cd "$GW" && docker compose up -d --force-recreate app >/dev/null)
ok=0
for i in $(seq 1 30); do
  H="$(curl -s -m 3 "http://127.0.0.1:$PORT/health" || true)"
  if echo "$H" | grep -q '"modems_online":[1-9]'; then ok=1; break; fi
  sleep 2
done
echo "  $H"
if [ "$ok" -eq 1 ]; then echo "  Modem gateway'da: OK"; else
  echo "  Modem hali ko'rinmadi. Tekshiring: docker logs --tail 40 sms_gateway ; ls -l /dev/ttyUSB*"; fi

say "5/5 eduziyo'ni qayta yig'ish"
echo "Yangi kod eduziyo konteynerlariga faqat qayta yig'ilgach tushadi (deploy/deploy.sh)."
if [ "$YES" -ne 1 ]; then
  read -r -p "Hozir 'bash deploy/deploy.sh' ishga tushirilsinmi? [y/N] " a
else a=y; fi
if [ "$a" = "y" ] || [ "$a" = "Y" ]; then (cd "$EDU" && bash deploy/deploy.sh); else
  echo "Keyin o'zingiz bajaring:  cd $EDU && bash deploy/deploy.sh"; fi

say "Tayyor"
echo "Tekshirish: eduziyo'dan o'z raqamingizga SMS yuboring; https://sms.elshod.date/admin/messages da 'Eduziyo' loyihasi ko'rinadi."
echo "Orqaga qaytarish: eduziyo/.env da MODEM_ENABLED=1, SMS_GATEWAY_KEY= (bo'sh), so'ng deploy.sh. Zaxira: $EDU/.gateway-backup-*"
