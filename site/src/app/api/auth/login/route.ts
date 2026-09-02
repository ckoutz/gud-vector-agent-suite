import { NextResponse } from "next/server";

import {
  bootstrapPortalUser,
  createSession,
  verifyPassword,
} from "@/lib/auth";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { claimQuoteForCustomer, getCustomerByEmail } from "@/lib/store";

export async function POST(request: Request) {
  if (!rateLimit(`login:${clientKey(request)}`, 8, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many login tries. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: { email?: string; password?: string; quote?: string };
  try {
    body = (await request.json()) as {
      email?: string;
      password?: string;
      quote?: string;
    };
  } catch {
    return NextResponse.json({ error: "Invalid login data." }, { status: 400 });
  }

  const email = body.email?.trim().toLowerCase() ?? "";
  const password = body.password ?? "";
  if (!email || !password) {
    return NextResponse.json(
      { error: "Email and password are required." },
      { status: 400 },
    );
  }

  try {
    await bootstrapPortalUser();
  } catch {
    // Store may be unavailable; login still proceeds against existing records.
  }

  const customer = await getCustomerByEmail(email);
  if (!customer || !(await verifyPassword(password, customer.passwordHash))) {
    return NextResponse.json(
      { error: "That email or password did not match." },
      { status: 401 },
    );
  }

  try {
    await createSession({
      customerId: customer.id,
      email: customer.email,
      role: customer.role === "admin" ? "admin" : "client",
    });
  } catch {
    return NextResponse.json(
      { error: "Login is not configured on this server yet." },
      { status: 503 },
    );
  }

  if (body.quote && customer.role !== "admin") {
    await claimQuoteForCustomer(body.quote, customer);
  }

  return NextResponse.json({ ok: true });
}
