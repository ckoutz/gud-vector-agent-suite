import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { createBillingPortalSession, paymentsReady } from "@/lib/stripe";
import { getQuoteByToken } from "@/lib/store";

export async function POST(request: Request) {
  if (!paymentsReady()) {
    return NextResponse.json(
      { error: "Payments are being connected." },
      { status: 503 },
    );
  }

  let claimToken = "";
  try {
    const body = (await request.json()) as { claimToken?: string };
    claimToken = body.claimToken?.trim() ?? "";
  } catch {
    claimToken = "";
  }

  if (claimToken) {
    const quote = await getQuoteByToken(claimToken);
    const customerId = quote?.customerId;
    if (!customerId) {
      return NextResponse.json(
        { error: "Billing is available after the first payment." },
        { status: 409 },
      );
    }
    const portal = await createBillingPortalSession(customerId);
    if (!portal?.url) {
      return NextResponse.json(
        { error: "Billing is available after the first payment." },
        { status: 409 },
      );
    }
    return NextResponse.json({ url: portal.url });
  }

  const { session, error } = await requireCustomer();
  if (error || !session) return error;

  const portal = await createBillingPortalSession(session.customerId);
  if (!portal?.url) {
    return NextResponse.json(
      { error: "Billing is available after the first payment." },
      { status: 409 },
    );
  }

  return NextResponse.json({ url: portal.url });
}
