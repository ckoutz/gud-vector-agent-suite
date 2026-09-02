import { NextResponse } from "next/server";

import { sendContactMessage } from "@/lib/mail";
import { clientKey, rateLimit } from "@/lib/rate-limit";
import { site } from "@/lib/site";
import { saveContact, type Need } from "@/lib/store";

const needs = new Set<string>(site.needs);

export async function POST(request: Request) {
  if (!rateLimit(`contact:${clientKey(request)}`, 5, 10 * 60 * 1000)) {
    return NextResponse.json(
      { error: "Too many messages from this network. Wait a few minutes." },
      { status: 429 },
    );
  }

  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid form data." }, { status: 400 });
  }

  if (typeof body.website === "string" && body.website.trim()) {
    return NextResponse.json({ error: "Unable to submit the form." }, { status: 400 });
  }

  const name = typeof body.name === "string" ? body.name.trim() : "";
  const email = typeof body.email === "string" ? body.email.trim() : "";
  const businessName =
    typeof body.businessName === "string" ? body.businessName.trim() : "";
  const need = typeof body.need === "string" ? body.need.trim() : "";
  const message = typeof body.message === "string" ? body.message.trim() : "";

  if (!name || !email || !need || !message) {
    return NextResponse.json(
      { error: "Name, email, need, and message are required." },
      { status: 400 },
    );
  }
  if (!needs.has(need)) {
    return NextResponse.json({ error: "Choose a valid need." }, { status: 400 });
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "Enter a valid email." }, { status: 400 });
  }

  const payload = {
    name,
    email,
    businessName: businessName || null,
    need: need as Need,
    message,
  };

  const mailed = await sendContactMessage({
    name,
    email,
    businessName,
    need,
    message,
  });

  if (mailed.ok) {
    try {
      await saveContact(payload);
    } catch {
      // Email already queued; still a success.
    }
    return NextResponse.json({ ok: true });
  }

  try {
    await saveContact(payload);
    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json(
      {
        error:
          "We could not send or store that message right now. Copy info@gudvector.com and try later.",
      },
      { status: 503 },
    );
  }
}
