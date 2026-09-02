import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { canAccessQuote } from "@/lib/auth";
import { getQuoteById, updateQuote } from "@/lib/store";

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role === "admin") {
    return NextResponse.json({ error: "Shop accounts do not reject quotes." }, { status: 403 });
  }

  const { id } = await context.params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }
  if (quote.status !== "sent" && quote.status !== "draft") {
    return NextResponse.json(
      { error: "This quote is no longer waiting for a response." },
      { status: 409 },
    );
  }

  await updateQuote(quote.id, { status: "rejected" });
  return NextResponse.json({ ok: true });
}
