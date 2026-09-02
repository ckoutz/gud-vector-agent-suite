import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { canAccessQuote } from "@/lib/auth";
import { getQuoteById, updateQuote, attachQuoteToCustomer, ensureInvoiceForQuote } from "@/lib/store";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role === "admin") {
    return NextResponse.json({ error: "Shop accounts do not accept quotes." }, { status: 403 });
  }

  const { id } = await context.params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }
  if (quote.status !== "sent" && quote.status !== "draft") {
    return NextResponse.json(
      { error: "This quote is no longer waiting for acceptance." },
      { status: 409 },
    );
  }

  await updateQuote(quote.id, { status: "accepted" });
  if (!quote.customerId) {
    await attachQuoteToCustomer(quote.id, session.customerId);
  }
  const latest = await getQuoteById(quote.id);
  if (latest) await ensureInvoiceForQuote(latest);
  return NextResponse.json({ ok: true });
}
