import { NextResponse } from "next/server";

import { getQuoteByToken, updateQuoteDetails } from "@/lib/store";

export async function POST(request: Request) {
  let body: {
    token?: string;
    name?: string;
    email?: string;
    phone?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const token = body.token?.trim() ?? "";
  const quote = token ? await getQuoteByToken(token) : null;
  if (!quote) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }

  const name = body.name?.trim() ?? "";
  const email = body.email?.trim().toLowerCase() ?? "";
  const phone = body.phone?.trim() || null;

  if (!name && !email && !phone) {
    return NextResponse.json(
      { error: "Add a name, email, or phone so we can set up service." },
      { status: 400 },
    );
  }

  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "Enter a valid email." }, { status: 400 });
  }

  try {
    await updateQuoteDetails(quote.id, {
      customerPhone: phone,
      customerName: name || null,
      customerEmail: email || null,
    });

    return NextResponse.json({ ok: true });
  } catch {
    return NextResponse.json(
      { error: "Could not save those details." },
      { status: 503 },
    );
  }
}
