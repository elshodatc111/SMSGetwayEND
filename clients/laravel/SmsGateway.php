<?php

namespace App\Services;

use Illuminate\Http\Client\RequestException;
use Illuminate\Support\Facades\Http;

/**
 * SMS Gateway klienti (Laravel).
 *
 * 1) Shu faylni app/Services/SmsGateway.php ga qo'ying.
 * 2) config/services.php ga qo'shing:
 *      'sms_gateway' => [
 *          'url'     => env('SMS_GATEWAY_URL', 'https://sms.elshod.date'),
 *          'key'     => env('SMS_GATEWAY_KEY'),
 *          'secret'  => env('SMS_GATEWAY_WEBHOOK_SECRET'),   // webhook ishlatilsa
 *      ],
 * 3) .env ga SMS_GATEWAY_URL va SMS_GATEWAY_KEY yozing.
 *
 * Ishlatish:
 *      $res = app(\App\Services\SmsGateway::class)->send('+998901234567', "Tasdiqlash kodi: $code", "otp-$userId-" . time());
 *      // $res['id'], $res['status'] === 'queued'
 */
class SmsGateway
{
    public function __construct(
        private ?string $baseUrl = null,
        private ?string $apiKey = null,
    ) {
        $this->baseUrl = rtrim($baseUrl ?? config('services.sms_gateway.url'), '/');
        $this->apiKey = $apiKey ?? config('services.sms_gateway.key');
    }

    private function client()
    {
        return Http::withToken($this->apiKey)
            ->acceptJson()
            ->timeout(10)
            ->retry(2, 300, throw: false);
    }

    /** SMS yuboradi (navbatga qo'yadi). Xato bo'lsa SmsGatewayException tashlaydi. */
    public function send(string $to, string $text, ?string $externalId = null): array
    {
        $payload = ['to' => $to, 'text' => $text];
        if ($externalId) {
            $payload['external_id'] = $externalId;
        }
        return $this->handle($this->client()->post("{$this->baseUrl}/api/v1/sms/send", $payload));
    }

    public function status(int $id): array
    {
        return $this->handle($this->client()->get("{$this->baseUrl}/api/v1/sms/{$id}"));
    }

    public function cancel(int $id): array
    {
        return $this->handle($this->client()->delete("{$this->baseUrl}/api/v1/sms/{$id}"));
    }

    public function usage(): array
    {
        return $this->handle($this->client()->get("{$this->baseUrl}/api/v1/me/usage"));
    }

    /**
     * Webhook imzosini tekshiradi.
     * Controller'da: SmsGateway::verifyWebhook(config('services.sms_gateway.secret'), $request)
     */
    public static function verifyWebhook(string $secret, \Illuminate\Http\Request $request, int $tolerance = 300): bool
    {
        $ts = (string) $request->header('X-SMS-Timestamp');
        $sig = (string) $request->header('X-SMS-Signature');
        if (!ctype_digit($ts) || abs(time() - (int) $ts) > $tolerance) {
            return false;
        }
        $expected = 'sha256=' . hash_hmac('sha256', $ts . '.' . $request->getContent(), $secret);
        return hash_equals($expected, $sig);
    }

    private function handle($response): array
    {
        if ($response->successful()) {
            return $response->json() ?? [];
        }
        $err = $response->json('error') ?? [];
        throw new SmsGatewayException(
            $err['message'] ?? 'SMS Gateway xatosi',
            $response->status(),
            $err['code'] ?? 'http_error',
        );
    }
}

class SmsGatewayException extends \RuntimeException
{
    public function __construct(string $message, public int $httpStatus = 0, public string $errorCode = '')
    {
        parent::__construct($message, $httpStatus);
    }
}
