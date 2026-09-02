/**
 * This install only. Server-side default for TWILIO_FROM_NUMBER.
 * Never render on the marketing homepage, footer, or contact —
 * public contact stays info@gudvector.com.
 */
export const INSTALL_TWILIO_FROM_NUMBER = "+19257019018";

export const INBOUND_SMS_WEBHOOK_URL = "https://gudvector.com/api/sms/inbound";

export { twilioAccountReady } from "@/lib/provider-flags";

export function twilioFromNumber() {
  return (
    process.env.TWILIO_FROM_NUMBER?.trim() ||
    process.env.TWILIO_PHONE_NUMBER?.trim() ||
    INSTALL_TWILIO_FROM_NUMBER
  );
}

export function twilioSmsReady() {
  return Boolean(
    process.env.TWILIO_ACCOUNT_SID?.trim() &&
      process.env.TWILIO_AUTH_TOKEN?.trim() &&
      twilioFromNumber(),
  );
}
