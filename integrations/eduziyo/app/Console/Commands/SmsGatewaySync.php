<?php

namespace App\Console\Commands;

use App\Models\SmsMessage;
use App\Services\Sms\GatewayDriver;
use Illuminate\Console\Command;

/** SMS Gateway navbatidagi xabarlar natijasini (yuborildi/xato) eduziyo bazasiga yozadi. Har daqiqada ishlaydi. */
class SmsGatewaySync extends Command
{
    protected $signature = 'sms:gateway-sync';

    protected $description = 'SMS Gateway orqali yuborilgan xabarlar holatini yangilaydi';

    public function handle(GatewayDriver $gateway): int
    {
        if (! $gateway->available()) {
            return self::SUCCESS;
        }

        $rows = SmsMessage::withoutGlobalScopes()
            ->where('status', SmsMessage::QUEUED)
            ->where('channel', 'modem')
            ->where('provider_response', 'like', GatewayDriver::MARK.'%')
            ->where('created_at', '>', now()->subDay())
            ->orderBy('id')
            ->limit(200)
            ->get();

        foreach ($rows as $sms) {
            $gateway->sync($sms);
        }

        if ($rows->isNotEmpty()) {
            $this->info("Tekshirildi: {$rows->count()} ta xabar");
        }

        return self::SUCCESS;
    }
}
