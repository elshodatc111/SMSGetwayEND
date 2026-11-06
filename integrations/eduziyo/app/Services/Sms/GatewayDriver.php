<?php

namespace App\Services\Sms;

use App\Contracts\SmsDriver;
use App\Models\SmsMessage;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;
use Throwable;

/**
 * SMS Gateway kanali (sms.elshod.date): USB modemlar eduziyo'da emas, alohida SMS Gateway xizmatida ishlaydi.
 *
 * Oqim: xabar gateway navbatiga qo'yiladi (external_id = "eduziyo-{id}"), natija `sms:gateway-sync` bilan
 * yangilanadi. Gateway javob bermasa, modem yo'q yoki limit tugagan bo'lsa xabar darhol Eskiz'ga o'tkaziladi.
 * Belgi: `channel = modem`, `provider_response` "Gateway" so'zi bilan boshlanadi.
 */
class GatewayDriver implements SmsDriver
{
    public const MARK = 'Gateway';

    /** Gateway shuncha daqiqada natija bermasa, xabar Eskiz'ga o'tkaziladi. */
    public const STUCK_MINUTES = 30;

    /** Qayta urinishning foydasi yo'q xatolar (xabar "failed" bo'ladi, Eskiz'ga o'tmaydi). */
    private const FATAL_CODES = ['invalid_phone', 'empty_text', 'text_too_long'];

    public function name(): string
    {
        return 'gateway';
    }

    public function available(): bool
    {
        return filled(config('services.sms_gateway.url')) && filled(config('services.sms_gateway.key'));
    }

    public static function externalId(SmsMessage $sms): string
    {
        return 'eduziyo-'.$sms->id;
    }

    public function send(SmsMessage $sms): void
    {
        $sms->update(['channel' => 'modem', 'provider_response' => self::MARK.': navbatga yuborilmoqda']);

        try {
            $response = $this->http()->post('/api/v1/sms/send', [
                'to' => $sms->phone,
                'text' => $sms->secret ?? $sms->message,
                'external_id' => self::externalId($sms),
            ]);
        } catch (Throwable $e) {
            $this->fallback($sms, 'aloqa yo\'q ('.$e->getMessage().')');

            return;
        }

        if ($response->successful()) {
            $sms->update(['provider_response' => self::MARK.': navbatda #'.$response->json('id')]);

            return;
        }

        $code = (string) $response->json('error.code');
        $message = (string) ($response->json('error.message') ?? 'HTTP '.$response->status());

        if (in_array($code, self::FATAL_CODES, true)) {
            $sms->update(['status' => SmsMessage::FAILED, 'provider_response' => mb_substr(self::MARK.': '.$message, 0, 250), 'secret' => null]);

            return;
        }

        $this->fallback($sms, $message);
    }

    /** Gateway'dagi holatni o'qib, xabarni yangilaydi (`sms:gateway-sync` chaqiradi). */
    public function sync(SmsMessage $sms): void
    {
        try {
            $response = $this->http()->get('/api/v1/sms', ['external_id' => self::externalId($sms), 'limit' => 1]);
        } catch (Throwable) {
            return; // tarmoq vaqtincha yo'q: keyingi daqiqada qayta
        }

        if (! $response->successful()) {
            return;
        }

        $item = $response->json('items.0');
        $old = $sms->created_at && $sms->created_at->lt(now()->subMinutes(self::STUCK_MINUTES));

        if (! is_array($item)) {
            if ($old) {
                $this->fallback($sms, 'xabar gateway\'da topilmadi');
            }

            return;
        }

        switch ($item['status'] ?? '') {
            case 'sent':
                $sms->update([
                    'status' => SmsMessage::SENT,
                    'sent_at' => now(),
                    'provider_response' => self::MARK.': modem orqali yuborildi',
                    'secret' => null,
                ]);
                break;

            case 'cancelled':
                $sms->update(['status' => SmsMessage::FAILED, 'provider_response' => self::MARK.': bekor qilindi', 'secret' => null]);
                break;

            case 'failed':
            case 'expired':
                if ((int) ($item['parts_sent'] ?? 0) > 0) {
                    // qisman ketgan: Eskiz'ga o'tkazsak, takror SMS bo'ladi
                    $sms->update(['status' => SmsMessage::FAILED, 'provider_response' => mb_substr(self::MARK.': '.($item['error'] ?? 'qisman yuborildi'), 0, 250), 'secret' => null]);
                } else {
                    $this->fallback($sms, (string) ($item['error'] ?? $item['status']));
                }
                break;

            default: // queued / sending
                if ($old) {
                    $this->fallback($sms, 'natija '.self::STUCK_MINUTES.' daqiqada kelmadi');
                }
        }
    }

    /** Gateway ishlamasa: xabar Eskiz orqali ketadi. */
    private function fallback(SmsMessage $sms, string $reason): void
    {
        $sms->update(['channel' => 'eskiz', 'provider_response' => mb_substr(self::MARK.' ishlamadi ('.$reason.'), Eskiz\'ga o\'tkazildi', 0, 250)]);
        app(EskizDriver::class)->send($sms);
    }

    private function http(): PendingRequest
    {
        return Http::baseUrl(rtrim((string) config('services.sms_gateway.url'), '/'))
            ->withToken((string) config('services.sms_gateway.key'))
            ->acceptJson()
            ->timeout(10);
    }
}
