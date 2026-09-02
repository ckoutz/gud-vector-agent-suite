import twilio from "twilio";

import {
  INBOUND_SMS_WEBHOOK_URL,
  twilioAccountReady,
  twilioFromNumber,
} from "@/lib/twilio-env";

let configured = false;

/**
 * Points this install's Twilio number at POST /api/sms/inbound.
 * Uses TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN from the environment
 * (Vercel). No-ops when those keys are missing. Never logs tokens.
 */
export async function ensureIncomingSmsWebhook() {
  if (configured) return;
  if (!twilioAccountReady()) return;

  const sid = process.env.TWILIO_ACCOUNT_SID;
  const token = process.env.TWILIO_AUTH_TOKEN;
  const from = twilioFromNumber();
  if (!sid || !token || !from) return;

  try {
    const client = twilio(sid, token);
    const numbers = await client.incomingPhoneNumbers.list({
      phoneNumber: from,
      limit: 5,
    });
    const number = numbers[0];
    if (!number) return;
    if (
      number.smsUrl === INBOUND_SMS_WEBHOOK_URL &&
      (number.smsMethod || "POST").toUpperCase() === "POST"
    ) {
      configured = true;
      return;
    }
    await client.incomingPhoneNumbers(number.sid).update({
      smsUrl: INBOUND_SMS_WEBHOOK_URL,
      smsMethod: "POST",
    });
    configured = true;
  } catch {
    // Keys may be unset here; they belong in Vercel, not the repo.
  }
}
