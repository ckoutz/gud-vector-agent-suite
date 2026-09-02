import { NextResponse } from "next/server";
import type Stripe from "stripe";

import {
  getStripe,
  markQuotePaidFromIntent,
  persistStripeInvoice,
} from "@/lib/stripe";
import {
  getQuoteByCheckoutSession,
  getQuoteById,
  getQuoteBySubscription,
  updateQuote,
  type QuoteStatus,
} from "@/lib/store";

export const runtime = "nodejs";

function invoiceFromEvent(object: Stripe.Invoice | { invoice?: unknown }) {
  if ("object" in object && object.object === "invoice") {
    return object as Stripe.Invoice;
  }
  const nested = "invoice" in object ? object.invoice : null;
  if (nested && typeof nested === "object" && "id" in nested) {
    return nested as Stripe.Invoice;
  }
  return null;
}

export async function POST(request: Request) {
  const stripe = getStripe();
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!stripe || !secret) {
    return NextResponse.json(
      { error: "Stripe webhooks are not configured." },
      { status: 503 },
    );
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) {
    return NextResponse.json({ error: "Missing signature." }, { status: 400 });
  }

  const raw = await request.text();
  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(raw, signature, secret);
  } catch {
    return NextResponse.json({ error: "Invalid signature." }, { status: 400 });
  }

  if (event.type === "checkout.session.completed") {
    const session = event.data.object as Stripe.Checkout.Session;
    const quoteId =
      typeof session.metadata?.quoteId === "string"
        ? session.metadata.quoteId
        : null;
    const quote =
      (quoteId ? await getQuoteById(quoteId) : null) ||
      (session.id ? await getQuoteByCheckoutSession(session.id) : null);
    if (quote) {
      const patch: {
        status: QuoteStatus;
        stripeCheckoutSessionId: string;
        stripeSubscriptionId?: string | null;
      } = {
        status: "paid",
        stripeCheckoutSessionId: session.id,
      };
      if (typeof session.subscription === "string") {
        patch.stripeSubscriptionId = session.subscription;
      }
      await updateQuote(quote.id, patch);
    }
  }

  if (
    event.type === "invoice.paid" ||
    event.type === "invoice.payment_succeeded"
  ) {
    const invoice = invoiceFromEvent(
      event.data.object as Stripe.Invoice | { invoice?: unknown },
    );
    if (invoice) {
      await persistStripeInvoice(invoice);
    } else if (
      "invoice" in event.data.object &&
      typeof (event.data.object as { invoice?: unknown }).invoice === "string"
    ) {
      const retrieved = await stripe.invoices.retrieve(
        (event.data.object as { invoice: string }).invoice,
      );
      await persistStripeInvoice(retrieved);
    }
  }

  if (event.type === "payment_intent.succeeded") {
    await markQuotePaidFromIntent(event.data.object as Stripe.PaymentIntent);
  }

  if (
    event.type === "customer.subscription.updated" ||
    event.type === "customer.subscription.deleted"
  ) {
    const subscription = event.data.object as Stripe.Subscription;
    const quote =
      (await getQuoteBySubscription(subscription.id)) ||
      (subscription.metadata?.quoteId
        ? await getQuoteById(subscription.metadata.quoteId)
        : null);
    if (quote) {
      let status: QuoteStatus = quote.status;
      if (event.type === "customer.subscription.deleted") {
        status = "canceled";
      } else if (subscription.pause_collection) {
        status = "paused";
      } else if (subscription.status === "canceled") {
        status = "canceled";
      } else if (
        subscription.status === "active" ||
        subscription.status === "trialing"
      ) {
        status = "paid";
      }
      await updateQuote(quote.id, {
        status,
        stripeSubscriptionId: subscription.id,
      });
    }
  }

  return NextResponse.json({ received: true });
}
