import { NextResponse } from "next/server";

import { checkAdminPassword, createAdminSession } from "@/lib/auth";
import { clientKey, rateLimit } from "@/lib/rate-limit";

export async function POST(request: Request) {
  if (!rateLimit(`admin:${clientKey(request)}`, 8, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many login tries. Wait a few minutes." },
      { status: 429 },
    );
  }

  if (!process.env.ADMIN_PASSWORD) {
    return NextResponse.json(
      { error: "Admin access is not configured." },
      { status: 503 },
    );
  }

  let body: { password?: string };
  try {
    body = (await request.json()) as { password?: string };
  } catch {
    return NextResponse.json({ error: "Invalid login data." }, { status: 400 });
  }

  if (!checkAdminPassword(body.password ?? "")) {
    return NextResponse.json({ error: "That password did not match." }, { status: 401 });
  }

  try {
    await createAdminSession();
  } catch {
    return NextResponse.json(
      { error: "Admin login is not configured on this server yet." },
      { status: 503 },
    );
  }

  return NextResponse.json({ ok: true });
}
