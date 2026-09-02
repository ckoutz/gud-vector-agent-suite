import { NextResponse } from "next/server";

import { getQuoteByToken, updateQuote } from "@/lib/store";

export async function POST(request: Request) {
  let body: { token?: string };
  try {
    body = (await request.json()) as { token?: string };
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const token = body.token?.trim() ?? "";
  const quote = token ? await getQuoteByToken(token) : null;
  if (!quote) {
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
