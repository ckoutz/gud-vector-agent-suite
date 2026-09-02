import { NextResponse } from "next/server";

import { completeEndUserSignup } from "@/lib/end-user-auth";
import { looksLikeEmail, toE164 } from "@/lib/phone";
import { readPhoneVerifiedToken } from "@/lib/phone-auth";
import { clientKey, rateLimit } from "@/lib/rate-limit";

export async function POST(request: Request) {
  if (!rateLimit(`phone-complete:${clientKey(request)}`, 10, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many tries. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: {
    token?: string;
    name?: string;
    businessName?: string;
    email?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const parsed = await readPhoneVerifiedToken(body.token?.trim() || "");
  if (!parsed) {
    return NextResponse.json(
      { error: "That phone code expired. Text a new one." },
      { status: 401 },
    );
  }

  const name = body.name?.trim() || "";
  const businessName = body.businessName?.trim() || "";
  const email = body.email?.trim().toLowerCase() || "";
  if (!name || !businessName || !email) {
    return NextResponse.json(
      { error: "Name, business name, and email are required." },
      { status: 400 },
    );
  }
  if (!looksLikeEmail(email)) {
    return NextResponse.json(
      { error: "Enter a valid email." },
      { status: 400 },
    );
  }

  const phone = toE164(parsed.phone);
  if (!phone) {
    return NextResponse.json(
      { error: "That phone number is not valid." },
      { status: 400 },
    );
  }

  const result = await completeEndUserSignup({
    phone,
    name,
    businessName,
    email,
    quoteToken: parsed.quote || null,
    phoneVerified: true,
  });
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }
  return NextResponse.json({ ok: true });
}
