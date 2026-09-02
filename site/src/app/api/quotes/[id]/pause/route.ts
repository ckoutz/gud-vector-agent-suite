import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { canAccessQuote } from "@/lib/auth";
import { sendServiceChangeNotice } from "@/lib/mail";
import { canPauseService, quoteService } from "@/lib/quote-desk";
import { pauseQuoteSubscription } from "@/lib/stripe";
import { getQuoteById } from "@/lib/store";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role === "admin") {
    return NextResponse.json({ error: "Shop accounts do not pause here." }, { status: 403 });
  }

  const { id } = await context.params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }
  if (!canPauseService(quote)) {
    return NextResponse.json(
      { error: "This service cannot be paused." },
      { status: 409 },
    );
  }

  try {
    await pauseQuoteSubscription(quote);
  } catch {
    return NextResponse.json(
      { error: "Could not pause this service." },
      { status: 503 },
    );
  }

  await sendServiceChangeNotice({
    customerName: quote.customerName || session.email,
    customerEmail: quote.customerEmail || session.email,
    service: quoteService(quote),
    action: "paused",
  });

  return NextResponse.json({ ok: true });
}
