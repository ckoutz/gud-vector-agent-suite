import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { hashPassword } from "@/lib/auth";
import { randomPassword } from "@/lib/ids";
import { notifyQuoteAcceptLink } from "@/lib/notify";
import { quoteService } from "@/lib/quote-desk";
import {
  createCustomer,
  createQuote,
  getCustomerByEmail,
  type Billing,
} from "@/lib/store";
import { appUrl, quoteSignupUrl } from "@/lib/urls";

export async function POST(request: Request) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role !== "admin") {
    return NextResponse.json({ error: "Shop login required." }, { status: 403 });
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
  const customerName = body.customerName?.trim() ?? "";
  if (!customerName || !service || !Number.isFinite(amountCents) || amountCents <= 0) {
    return NextResponse.json(
      { error: "Customer, service, and a price greater than zero are required." },
      { status: 400 },
    );
  }

  const email = body.customerEmail?.trim().toLowerCase() || null;
  let customerId: string | null = null;
  if (email) {
    const existing = await getCustomerByEmail(email);
    if (existing && existing.role !== "admin") {
      customerId = existing.id;
    } else if (!existing) {
      const created = await createCustomer({
        email,
        name: customerName,
        passwordHash: await hashPassword(randomPassword()),
        phone: body.customerPhone?.trim() || null,
        role: "client",
      });
      customerId = created.id;
    }
  }

  const quote = await createQuote({
    customerId,
    amountCents: Math.round(amountCents),
    billing: body.billing === "monthly" ? "monthly" : "one_time",
    status: "sent",
    customerName,
    customerPhone: body.customerPhone?.trim() || null,
    customerEmail: email,
    serviceAddress: body.serviceAddress?.trim() || null,
    items: [
      {
        description: service,
        quantity: 1,
        amountCents: Math.round(amountCents),
      },
    ],
  });

  await notifyQuoteAcceptLink({
    email,
    phone: quote.customerPhone,
    acceptUrl: quoteSignupUrl(quote.claimToken),
    loginUrl: `${appUrl()}/portal/login?quote=${encodeURIComponent(quote.claimToken)}`,
    service: quoteService(quote),
  });

  return NextResponse.json({ ok: true, id: quote.id });
}
