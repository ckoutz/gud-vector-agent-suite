import Stripe from "stripe";

import { appUrl } from "@/lib/urls";
import {
  getCustomerById,
  getCustomerByStripeId,
  getInvoiceByStripeId,
  getQuoteById,
  getQuoteByStripeInvoiceId,
  getQuoteBySubscription,
  localInvoiceId,
  PROTECH_STRIPE_CUSTOMER_ID,
  setCustomerStripeId,
  updateQuote,
  upsertClientInvoice,
  type ClientInvoice,
  type ClientInvoiceStatus,
  type Customer,
  type Quote,
  type QuoteWithCustomer,
} from "@/lib/store";

export function paymentsReady() {
  return Boolean(process.env.STRIPE_SECRET_KEY);
}

export function stripePublishableKey() {
  return (
    process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY ||
    process.env.STRIPE_PUBLISHABLE_KEY ||
    ""
  );
}

export function invoicePaymentsReady() {
  return paymentsReady() && Boolean(stripePublishableKey());
}

export function getStripe() {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) return null;
  return new Stripe(key);
}

async function ensureStripeCustomer(customer: Customer) {
  const stripe = getStripe();
  if (!stripe) return null;
  if (customer.email === "glen@protech-cal.com") {
    if (customer.stripeCustomerId !== PROTECH_STRIPE_CUSTOMER_ID) {
      await setCustomerStripeId(customer.id, PROTECH_STRIPE_CUSTOMER_ID);
    }
    return PROTECH_STRIPE_CUSTOMER_ID;
  }
  if (customer.stripeCustomerId) return customer.stripeCustomerId;
  const created = await stripe.customers.create({
    email: customer.email,
    name: customer.name,
    metadata: { customerId: customer.id },
  });
  await setCustomerStripeId(customer.id, created.id);
  return created.id;
}

function stripeCustomerIdOf(invoice: Stripe.Invoice) {
  const customer = invoice.customer;
  if (typeof customer === "string") return customer;
  if (customer && typeof customer === "object" && "id" in customer) {
    return customer.id;
  }
  return null;
}

function invoiceDescription(invoice: Stripe.Invoice) {
  if (invoice.description) return invoice.description;
  const line = invoice.lines?.data?.[0];
  if (line?.description) return line.description;
  return "Güd Vector invoice";
}

function invoiceAmountCents(invoice: Stripe.Invoice) {
  if (invoice.status === "paid") {
    return invoice.amount_paid || invoice.total || 0;
  }
  return invoice.amount_due || invoice.total || 0;
}

function invoicePaidAt(invoice: Stripe.Invoice) {
  if (invoice.status !== "paid") return null;
  const paidAt = invoice.status_transitions?.paid_at;
  if (paidAt) return new Date(paidAt * 1000).toISOString();
  return new Date().toISOString();
}

export function clientInvoiceFromStripe(
  invoice: Stripe.Invoice,
  customerId?: string | null,
): Omit<ClientInvoice, "createdAt"> {
  const status = (invoice.status || "open") as ClientInvoiceStatus;
  const normalized: ClientInvoiceStatus =
    status === "draft" ||
    status === "open" ||
    status === "paid" ||
    status === "void" ||
    status === "uncollectible"
      ? status
      : "open";
  return {
    stripeInvoiceId: invoice.id,
    customerId: customerId ?? null,
    stripeCustomerId: stripeCustomerIdOf(invoice),
    quoteId: null,
    number: invoice.number ?? null,
    description: invoiceDescription(invoice),
    amountCents: invoiceAmountCents(invoice),
    currency: invoice.currency || "usd",
    status: normalized,
    paidMethod: normalized === "paid" ? "card" : null,
    paidNote: null,
    hostedInvoiceUrl: invoice.hosted_invoice_url ?? null,
    paidAt: invoicePaidAt(invoice),
  };
}

function stripeSubscriptionIdOf(invoice: Stripe.Invoice) {
  const raw = invoice as Stripe.Invoice & {
    subscription?: string | { id?: string } | null;
    parent?: {
      type?: string | null;
      subscription_details?: { subscription?: string | { id?: string } | null };
    } | null;
  };
  const fromField = raw.subscription;
  if (typeof fromField === "string") return fromField;
  if (fromField && typeof fromField === "object" && fromField.id) {
    return fromField.id;
  }
  const nested = raw.parent?.subscription_details?.subscription;
  if (typeof nested === "string") return nested;
  if (nested && typeof nested === "object" && nested.id) return nested.id;
  return null;
}

export async function persistStripeInvoice(
  invoice: Stripe.Invoice,
  customerId?: string | null,
) {
  let localCustomerId = customerId ?? null;
  const stripeCustomerId = stripeCustomerIdOf(invoice);
  if (!localCustomerId && stripeCustomerId) {
    const local = await getCustomerByStripeId(stripeCustomerId);
    localCustomerId = local?.id ?? null;
  }
  const existing = await getInvoiceByStripeId(invoice.id);
  const incoming = clientInvoiceFromStripe(invoice, localCustomerId);
  const inPerson =
    existing?.status === "paid" &&
    (existing.paidMethod === "cash" || existing.paidMethod === "check");
  if (inPerson && incoming.status !== "paid") {
    return existing;
  }

  const record = await upsertClientInvoice({
    ...incoming,
    quoteId: existing?.quoteId ?? incoming.quoteId,
    paidMethod: incoming.status === "paid" ? "card" : null,
    paidNote: incoming.status === "paid" ? existing?.paidNote ?? null : null,
  });

  if (record?.status === "paid") {
    const byInvoice = await getQuoteByStripeInvoiceId(record.stripeInvoiceId);
    const subscriptionId = stripeSubscriptionIdOf(invoice);
    const bySubscription = subscriptionId
      ? await getQuoteBySubscription(subscriptionId)
      : null;
    const quote = byInvoice || bySubscription;
    if (quote && quote.status !== "canceled" && quote.status !== "paused") {
      await updateQuote(quote.id, {
        status: "paid",
        stripeInvoiceId: quote.stripeInvoiceId || record.stripeInvoiceId,
        stripeSubscriptionId: quote.stripeSubscriptionId || subscriptionId,
      });
    }
  }
  return record;
}

export async function refreshClientInvoices(customer: Customer) {
  const stripe = getStripe();
  if (!stripe || !customer.stripeCustomerId) return;
  try {
    const listed = await stripe.invoices.list({
      customer: customer.stripeCustomerId,
      limit: 40,
    });
    for (const invoice of listed.data) {
      await persistStripeInvoice(invoice, customer.id);
    }
  } catch {
    // Keep the locally seeded invoice if Stripe is unset or unreachable.
  }
}

function clientSecretFromInvoice(invoice: Stripe.Invoice) {
  const confirmation = (
    invoice as Stripe.Invoice & {
      confirmation_secret?: { client_secret?: string | null } | null;
    }
  ).confirmation_secret;
  if (confirmation?.client_secret) return confirmation.client_secret;

  const paymentIntent = (
    invoice as Stripe.Invoice & {
      payment_intent?: string | Stripe.PaymentIntent | null;
    }
  ).payment_intent;
  if (
    paymentIntent &&
    typeof paymentIntent === "object" &&
    paymentIntent.client_secret
  ) {
    return paymentIntent.client_secret;
  }
  return null;
}

async function retrieveInvoiceForPayment(stripe: Stripe, invoiceId: string) {
  try {
    return await stripe.invoices.retrieve(invoiceId, {
      expand: ["confirmation_secret"],
    });
  } catch {
    return stripe.invoices.retrieve(invoiceId);
  }
}

export async function createInvoicePaymentElement(
  stripeInvoiceId: string,
  customer: Customer,
) {
  const stripe = getStripe();
  const publishableKey = stripePublishableKey();
  if (!stripe || !publishableKey) return null;

  const local = await getInvoiceByStripeId(stripeInvoiceId);
  if (local?.status === "paid") {
    return { alreadyPaid: true as const, clientSecret: null, publishableKey };
  }

  let invoice: Stripe.Invoice;
  try {
    invoice = await retrieveInvoiceForPayment(stripe, stripeInvoiceId);
  } catch {
    return null;
  }

  const invoiceCustomer = stripeCustomerIdOf(invoice);
  if (
    invoiceCustomer &&
    customer.stripeCustomerId &&
    invoiceCustomer !== customer.stripeCustomerId
  ) {
    return null;
  }

  await persistStripeInvoice(invoice, customer.id);

  if (invoice.status === "paid") {
    return { alreadyPaid: true as const, clientSecret: null, publishableKey };
  }

  let clientSecret = clientSecretFromInvoice(invoice);
  if (!clientSecret) {
    const paymentIntent = await stripe.paymentIntents.create({
      amount: invoiceAmountCents(invoice),
      currency: invoice.currency || "usd",
      customer: invoiceCustomer || customer.stripeCustomerId || undefined,
      description: invoiceDescription(invoice),
      metadata: {
        stripeInvoiceId: invoice.id,
        customerId: customer.id,
        kind: "gud_vector_invoice",
      },
      automatic_payment_methods: {
        enabled: true,
        allow_redirects: "never",
      },
    });
    clientSecret = paymentIntent.client_secret;
    try {
      if (paymentIntent.id) {
        await stripe.invoices.attachPayment(invoice.id, {
          payment_intent: paymentIntent.id,
        });
      }
    } catch {
      // Webhook payment_intent.succeeded still marks the local invoice paid.
    }
  }

  if (!clientSecret) return null;
  return { alreadyPaid: false as const, clientSecret, publishableKey };
}

export async function createQuotePaymentElement(
  quote: QuoteWithCustomer,
  customer: Customer,
) {
  if (quote.status === "paid") {
    return {
      alreadyPaid: true as const,
      clientSecret: null as string | null,
      publishableKey: stripePublishableKey(),
    };
  }
  const localId = quote.stripeInvoiceId || localInvoiceId(quote.id);
  const local = await getInvoiceByStripeId(localId);
  if (local?.status === "paid") {
    return {
      alreadyPaid: true as const,
      clientSecret: null as string | null,
      publishableKey: stripePublishableKey(),
    };
  }
  if (quote.stripeInvoiceId) {
    return createInvoicePaymentElement(quote.stripeInvoiceId, customer);
  }

  const stripe = getStripe();
  const publishableKey = stripePublishableKey();
  if (!stripe || !publishableKey) return null;
  const stripeCustomer = await ensureStripeCustomer(customer);
  const description = quote.items[0]?.description || "Service";

  if (!stripeCustomer) return null;

  if (quote.billing === "monthly") {
    const product = await stripe.products.create({
      name: description,
      metadata: { quoteId: quote.id },
    });
    const price = await stripe.prices.create({
      product: product.id,
      currency: quote.currency,
      unit_amount: quote.amountCents,
      recurring: { interval: "month" },
    });
    const subscription = await stripe.subscriptions.create({
      customer: stripeCustomer,
      items: [{ price: price.id }],
      payment_behavior: "default_incomplete",
      payment_settings: { save_default_payment_method: "on_subscription" },
      expand: ["latest_invoice.confirmation_secret", "latest_invoice.payment_intent"],
      metadata: { quoteId: quote.id, customerId: customer.id, billing: "monthly" },
    });
    await updateQuote(quote.id, { stripeSubscriptionId: subscription.id });
    const latest = subscription.latest_invoice;
    const invoice =
      latest && typeof latest === "object"
        ? (latest as Stripe.Invoice & {
            confirmation_secret?: { client_secret?: string | null };
          })
        : null;
    const clientSecret = invoice
      ? invoice.confirmation_secret?.client_secret ||
        clientSecretFromInvoice(invoice)
      : null;
    if (!clientSecret) return null;
    return { alreadyPaid: false as const, clientSecret, publishableKey };
  }

  const paymentIntent = await stripe.paymentIntents.create({
    amount: quote.amountCents,
    currency: quote.currency,
    customer: stripeCustomer || undefined,
    description,
    metadata: {
      quoteId: quote.id,
      customerId: customer.id,
      kind: "quote_payment",
    },
    automatic_payment_methods: {
      enabled: true,
      allow_redirects: "never",
    },
  });
  if (!paymentIntent.client_secret) return null;
  return {
    alreadyPaid: false as const,
    clientSecret: paymentIntent.client_secret,
    publishableKey,
  };
}

export async function pauseQuoteSubscription(quote: Quote) {
  const stripe = getStripe();
  if (stripe && quote.stripeSubscriptionId) {
    await stripe.subscriptions.update(quote.stripeSubscriptionId, {
      pause_collection: { behavior: "void" },
    });
  }
  await updateQuote(quote.id, { status: "paused" });
}

export async function cancelQuoteSubscription(quote: Quote) {
  const stripe = getStripe();
  if (stripe && quote.stripeSubscriptionId) {
    await stripe.subscriptions.cancel(quote.stripeSubscriptionId);
  }
  await updateQuote(quote.id, { status: "canceled" });
}

export async function markQuotePaidFromIntent(paymentIntent: Stripe.PaymentIntent) {
  const quoteId =
    typeof paymentIntent.metadata?.quoteId === "string"
      ? paymentIntent.metadata.quoteId
      : null;
  if (quoteId) {
    const quote = await getQuoteById(quoteId);
    if (quote && quote.status !== "canceled") {
      await updateQuote(quote.id, { status: "paid" });
    }
  }
  return syncPaidInvoiceFromPaymentIntent(paymentIntent);
}

export async function syncPaidInvoiceFromPaymentIntent(
  paymentIntent: Stripe.PaymentIntent,
) {
  const stripe = getStripe();
  const linked = (
    paymentIntent as Stripe.PaymentIntent & {
      invoice?: string | { id?: string } | null;
    }
  ).invoice;
  const invoiceId =
    (typeof linked === "string"
      ? linked
      : linked && typeof linked === "object"
        ? linked.id || null
        : null) ||
    (typeof paymentIntent.metadata?.stripeInvoiceId === "string"
      ? paymentIntent.metadata.stripeInvoiceId
      : null);
  if (!invoiceId || !stripe) return null;

  try {
    let invoice = await stripe.invoices.retrieve(invoiceId);
    if (
      paymentIntent.status === "succeeded" &&
      (invoice.status === "open" || invoice.status === "draft")
    ) {
      try {
        invoice = await stripe.invoices.pay(invoiceId, {
          paid_out_of_band: true,
        });
      } catch {
        invoice = await stripe.invoices.retrieve(invoiceId);
      }
    }
    return persistStripeInvoice(invoice);
  } catch {
    return null;
  }
}

export async function createCheckoutSession(
  quote: QuoteWithCustomer,
  urls?: { successUrl?: string; cancelUrl?: string },
) {
  const stripe = getStripe();
  if (!stripe) return null;
  const stripeCustomer = quote.customer
    ? await ensureStripeCustomer(quote.customer)
    : null;
  const origin = appUrl();
  const lineItems: Stripe.Checkout.SessionCreateParams.LineItem[] =
    quote.items.length > 0
      ? quote.items.map((item) => ({
          quantity: item.quantity,
          price_data: {
            currency: quote.currency,
            product_data: { name: item.description },
            unit_amount: item.amountCents,
            ...(quote.billing === "monthly"
              ? { recurring: { interval: "month" as const } }
              : {}),
          },
        }))
      : [
          {
            quantity: 1,
            price_data: {
              currency: quote.currency,
              product_data: { name: quote.items[0]?.description || "Quote" },
              unit_amount: quote.amountCents,
              ...(quote.billing === "monthly"
                ? { recurring: { interval: "month" as const } }
                : {}),
            },
          },
        ];

  const success =
    urls?.successUrl ||
    (quote.claimToken
      ? `${origin}/q/${quote.claimToken}?checkout=success`
      : `${origin}/portal?checkout=success`);
  const cancel =
    urls?.cancelUrl ||
    (quote.claimToken
      ? `${origin}/q/${quote.claimToken}?checkout=canceled`
      : `${origin}/portal?checkout=canceled`);

  return stripe.checkout.sessions.create({
    mode: quote.billing === "monthly" ? "subscription" : "payment",
    customer: stripeCustomer ?? undefined,
    customer_email:
      stripeCustomer || !quote.customer?.email
        ? undefined
        : quote.customer.email,
    line_items: lineItems,
    success_url: success,
    cancel_url: cancel,
    metadata: {
      quoteId: quote.id,
      customerId: quote.customerId ?? "",
      claimToken: quote.claimToken ?? "",
    },
    subscription_data:
      quote.billing === "monthly"
        ? { metadata: { quoteId: quote.id } }
        : undefined,
  });
}

export async function createBillingPortalSession(customerId: string) {
  const stripe = getStripe();
  if (!stripe) return null;
  const customer = await getCustomerById(customerId);
  if (!customer?.stripeCustomerId) return null;
  const returnUrl =
    process.env.STRIPE_BILLING_PORTAL_RETURN_URL || `${appUrl()}/portal`;
  return stripe.billingPortal.sessions.create({
    customer: customer.stripeCustomerId,
    return_url: returnUrl,
  });
}

export function quotePayable(quote: Quote) {
  return quote.status === "accepted";
}
