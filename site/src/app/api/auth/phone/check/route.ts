import { NextResponse } from "next/server";
import twilio from "twilio";

import {
  hasUsableEndUserIdentity,
  sessionForEndUser,
} from "@/lib/end-user-auth";
import { isPendingEmail, toE164 } from "@/lib/phone";
import { phoneVerifiedToken } from "@/lib/phone-auth";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { twilioVerifyReady } from "@/lib/provider-flags";
import {
  claimQuoteForCustomer,
  getCustomerByPhone,
} from "@/lib/store";
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
  if (!rateLimit(`phone-check:${clientKey(request)}`, 10, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many tries. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: {
    phone?: string;
    code?: string;
    quote?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const phone = toE164(body.phone);
  const code = body.code?.trim() ?? "";
  if (!phone || !code) {
    return NextResponse.json(
      { error: "Phone and code are required." },
      { status: 400 },
    );
  }

  try {
    const serviceSid = await getVerifyServiceSid();
    const client = twilio(
      process.env.TWILIO_ACCOUNT_SID,
      process.env.TWILIO_AUTH_TOKEN,
    );
    const check = await client.verify.v2
      .services(serviceSid)
      .verificationChecks.create({ to: phone, code });
    if (check.status !== "approved") {
      return NextResponse.json(
        { error: "That code did not match." },
        { status: 401 },
      );
    }
  } catch {
    return NextResponse.json(
      { error: "Could not check that code." },
      { status: 503 },
    );
  }

  const existing = await getCustomerByPhone(phone);
  if (existing?.role === "admin") {
    return NextResponse.json(
      { error: "Use the shop login for that account." },
      { status: 403 },
    );
  }

  if (existing && hasUsableEndUserIdentity(existing)) {
    if (body.quote) {
      await claimQuoteForCustomer(body.quote, existing);
    }
    await sessionForEndUser(existing);
    return NextResponse.json({ ok: true });
  }

  try {
    const token = await phoneVerifiedToken(phone, body.quote?.trim() || null);
    const suggestedName =
      existing?.name && existing.name.toLowerCase() !== "customer"
        ? existing.name
        : "";
    const suggestedEmail =
      existing?.email && !isPendingEmail(existing.email) ? existing.email : "";
    const suggestedBusiness = existing?.businessName || "";
    return NextResponse.json({
      ok: true,
      needsIdentity: true,
      token,
      name: suggestedName,
      email: suggestedEmail,
      businessName: suggestedBusiness,
    });
  } catch {
    return NextResponse.json(
      { error: "Could not continue phone signup." },
      { status: 503 },
    );
  }
}
