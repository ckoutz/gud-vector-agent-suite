import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { canAccessQuote } from "@/lib/auth";
import { deleteQuote, getQuoteById, saveQuoteEdits, type Billing } from "@/lib/store";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role !== "admin") {
    return NextResponse.json({ error: "Shop login required." }, { status: 403 });
  }

  const { id } = await context.params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }

  let body: {
    customerName?: string;
    customerPhone?: string;
    customerEmail?: string;
    serviceAddress?: string;
    service?: string;
    amountCents?: number;
    billing?: Billing;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const service = body.service?.trim() ?? "";
  const amountCents = Number(body.amountCents);
  if (!service || !Number.isFinite(amountCents) || amountCents <= 0) {
    return NextResponse.json(
      { error: "Service and a price greater than zero are required." },
      { status: 400 },
    );
  }

  await saveQuoteEdits(id, {
    customerName: body.customerName?.trim() ?? "",
    customerPhone: body.customerPhone?.trim() ?? "",
    customerEmail: body.customerEmail?.trim() ?? "",
    serviceAddress: body.serviceAddress?.trim() ?? "",
    service,
    amountCents: Math.round(amountCents),
    billing: body.billing === "monthly" ? "monthly" : "one_time",
  });

  return NextResponse.json({ ok: true });
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role !== "admin") {
    return NextResponse.json({ error: "Shop login required." }, { status: 403 });
  }

  const { id } = await context.params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) {
    return NextResponse.json({ error: "Quote not found." }, { status: 404 });
  }

  await deleteQuote(id);
  return NextResponse.json({ ok: true });
}
