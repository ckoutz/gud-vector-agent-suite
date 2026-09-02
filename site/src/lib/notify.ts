import twilio from "twilio";

import { sendEmail, sendQuoteInvite } from "@/lib/mail";
import { site } from "@/lib/site";
import { twilioAccountReady, twilioFromNumber } from "@/lib/twilio-env";

export function twilioReady() {
  return twilioAccountReady() && Boolean(twilioFromNumber());
}

export async function sendQuoteSms(to: string, body: string) {
  const sid = process.env.TWILIO_ACCOUNT_SID;
  const token = process.env.TWILIO_AUTH_TOKEN;
  const from = twilioFromNumber();
  if (!sid || !token || !from) {
    return { ok: false as const, skipped: true };
  }
  try {
    const client = twilio(sid, token);
    await client.messages.create({ to, from, body });
    return { ok: true as const, skipped: false };
  } catch {
    return { ok: false as const, skipped: false };
  }
}

export async function notifyNewClientQuote({
  email,
  phone,
  signupUrl,
  service,
}: {
  email?: string | null;
  phone?: string | null;
  signupUrl: string;
  service: string;
}) {
  const text = [
    `Your quote for ${service} is ready.`,
    `Create an account to view it and accept or reject:`,
    signupUrl,
  ].join("\n");

  let smsOk = false;
  if (phone) {
    const sms = await sendQuoteSms(phone, text.replaceAll("\n", " "));
    smsOk = sms.ok;
  }

  let emailed = false;
  if (email) {
    const result = await sendQuoteInvite({
      to: email,
      acceptUrl: signupUrl,
      loginUrl: signupUrl,
      service,
    });
    emailed = result.ok;
    if (!emailed) {
      const fallback = await sendEmail({
        to: email,
        subject: "Your quote is ready",
        text,
      });
      emailed = fallback.ok;
    }
  }

  return { smsOk, emailed };
}

export async function notifyQuoteAcceptLink({
  email,
  phone,
  acceptUrl,
  loginUrl,
  service,
}: {
  email?: string | null;
  phone?: string | null;
  acceptUrl: string;
  loginUrl: string;
  service: string;
}) {
  return notifyNewClientQuote({
    email,
    phone,
    signupUrl: acceptUrl || loginUrl,
    service,
  });
}
