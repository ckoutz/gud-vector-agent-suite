import { NextResponse } from "next/server";

import { completeEndUserSignup } from "@/lib/end-user-auth";
import { clientKey, rateLimit } from "@/lib/rate-limit";

export async function POST(request: Request) {
  if (!rateLimit(`signup:${clientKey(request)}`, 8, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many tries. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: {
    email?: string;
    password?: string;
    name?: string;
    phone?: string;
    quote?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const email = body.email?.trim().toLowerCase() ?? "";
  const password = body.password ?? "";
  if (!email || !password) {
    return NextResponse.json(
      { error: "Email and password are required." },
      { status: 400 },
    );
  }
  if (password.length < 8) {
    return NextResponse.json(
      { error: "Use a password of at least 8 characters." },
      { status: 400 },
    );
  }

  const result = await completeEndUserSignup({
    email,
    password,
    name: body.name,
    phone: body.phone,
    quoteToken: body.quote,
  });
  if (!result.ok) {
    return NextResponse.json({ error: result.error }, { status: result.status });
  }
  return NextResponse.json({ ok: true });
}
