import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { canAccessQuote } from "@/lib/auth";
import {
  getCustomerById,
  getInvoiceByStripeId,
  getQuoteById,
} from "@/lib/store";
import {
  createInvoicePaymentElement,
  createQuotePaymentElement,
  invoicePaymentsReady,
} from "@/lib/stripe";

export async function POST(request: Request) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role === "admin") {
    return NextResponse.json(
      { error: "Shop accounts do not pay here." },
      { status: 403 },
    );
  }

  let body: { invoiceId?: string; quoteId?: string };
  try {
    body = (await request.json()) as { invoiceId?: string; quoteId?: string };
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const customer = await getCustomerById(session.customerId);
  if (!customer) {
    return NextResponse.json({ error: "Please log in." }, { status: 401 });
  }

  if (!invoicePaymentsReady()) {
    return NextResponse.json(
      { error: "Payments are being connected." },
      { status: 503 },
    );
  }

  const quoteId = body.quoteId?.trim();
  if (quoteId) {
    const quote = await getQuoteById(quoteId);
    if (!quote || !canAccessQuote(session, quote)) {
      return NextResponse.json({ error: "Quote not found." }, { status: 404 });
    }
    if (quote.status === "paid") {
      return NextResponse.json({ alreadyPaid: true });
    }
    if (quote.status !== "accepted") {
      return NextResponse.json(
        { error: "Accept this quote before paying." },
        { status: 409 },
      );
    }
    const result = await createQuotePaymentElement(quote, customer);
    if (!result) {
      return NextResponse.json(
        { error: "Payments are being connected." },
        { status: 503 },
      );
    }
    if (result.alreadyPaid) return NextResponse.json({ alreadyPaid: true });
    if (!result.clientSecret) {
      return NextResponse.json(
        { error: "Payments are being connected." },
        { status: 503 },
      );
    }
    return NextResponse.json({
      clientSecret: result.clientSecret,
      publishableKey: result.publishableKey,
    });
  }

  const invoiceId = body.invoiceId?.trim();
  if (!invoiceId) {
    return NextResponse.json({ error: "A quote or invoice is required." }, { status: 400 });
  }

  const local = await getInvoiceByStripeId(invoiceId);
  if (local) {
    if (local.customerId && local.customerId !== customer.id) {
      return NextResponse.json({ error: "Invoice not found." }, { status: 404 });
    }
    if (local.status === "paid") {
      return NextResponse.json({ alreadyPaid: true });
    }
  }

  const result = await createInvoicePaymentElement(invoiceId, customer);
  if (!result) {
    return NextResponse.json({ error: "Invoice not found." }, { status: 404 });
  }
  if (result.alreadyPaid) {
    return NextResponse.json({ alreadyPaid: true });
  }
  if (!result.clientSecret) {
    return NextResponse.json(
      { error: "Payments are being connected." },
      { status: 503 },
    );
  }
  return NextResponse.json({
    clientSecret: result.clientSecret,
    publishableKey: result.publishableKey,
  });
}
