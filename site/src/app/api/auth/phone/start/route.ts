import { NextResponse } from "next/server";
import twilio from "twilio";

import { toE164 } from "@/lib/phone";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { twilioVerifyReady } from "@/lib/provider-flags";
import { getVerifyServiceSid } from "@/lib/twilio-verify";

export async function POST(request: Request) {
  if (!twilioVerifyReady()) {
    return NextResponse.json(
      {
        error:
          "Phone signup is not configured. Set TWILIO_VERIFY_SERVICE_SID or VERIFY_SERVICE_SID, plus TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
      },
      { status: 503 },
    );
  }
  if (!rateLimit(`phone-start:${clientKey(request)}`, 6, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many codes. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: { phone?: string };
  try {
    body = (await request.json()) as { phone?: string };
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const phone = toE164(body.phone);
  if (!phone) {
    return NextResponse.json(
      { error: "Enter a valid US mobile number." },
      { status: 400 },
    );
  }

  try {
    const serviceSid = await getVerifyServiceSid();
    const client = twilio(
      process.env.TWILIO_ACCOUNT_SID,
      process.env.TWILIO_AUTH_TOKEN,
    );
    await client.verify.v2
      .services(serviceSid)
      .verifications.create({ to: phone, channel: "sms" });
  } catch {
    return NextResponse.json(
      { error: "Could not send a Verify code to that number." },
      { status: 503 },
    );
  }

  return NextResponse.json({ ok: true });
}
