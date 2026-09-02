import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { neon } from "@neondatabase/serverless";

import { createClaimToken, createId } from "@/lib/ids";
import {
  isPendingEmail,
  pendingEmailForPhone,
  phoneDigits,
  samePhone,
  toE164,
} from "@/lib/phone";
import { isExpiredSent } from "@/lib/quote-desk";

export const PENDING_PASSWORD_HASH = "pending";

export type Need = "Website" | "Automating business systems" | "Both";

export type QuoteStatus =
  | "draft"
  | "sent"
  | "accepted"
  | "rejected"
  | "acknowledged"
  | "paid"
  | "canceled"
  | "paused";
export type ServiceStatus = "active" | "inactive" | "complete";
export type Billing = "one_time" | "monthly";

export type ContactSubmission = {
  id: string;
  name: string;
  email: string;
  businessName: string | null;
  need: Need;
  message: string;
  createdAt: string;
};

export type QuoteSource = "admin" | "sms";

export type PortalRole = "client" | "admin";

export type Customer = {
  id: string;
  email: string;
  name: string;
  businessName: string | null;
  passwordHash: string;
  stripeCustomerId: string | null;
  phone: string | null;
  role: PortalRole;
  createdAt: string;
};

export type QuoteItem = {
  id: string;
  quoteId: string;
  description: string;
  quantity: number;
  amountCents: number;
};

export type Quote = {
  id: string;
  customerId: string | null;
  claimToken: string;
  status: QuoteStatus;
  amountCents: number;
  currency: string;
  dueDate: string | null;
  billing: Billing;
  serviceAddress: string | null;
  customerPhone: string | null;
  customerName: string | null;
  customerEmail: string | null;
  isSeed: boolean;
  source: QuoteSource;
  smsFromLast4: string | null;
  stripeCheckoutSessionId: string | null;
  stripeSubscriptionId: string | null;
  stripeInvoiceId: string | null;
  createdAt: string;
  items: QuoteItem[];
};

export type QuoteWithCustomer = Quote & { customer: Customer | null };

export type ClientInvoiceStatus =
  | "draft"
  | "open"
  | "paid"
  | "void"
  | "uncollectible";

export type InvoicePaidMethod = "card" | "cash" | "check";

export type ClientInvoice = {
  stripeInvoiceId: string;
  customerId: string | null;
  stripeCustomerId: string | null;
  quoteId: string | null;
  number: string | null;
  description: string;
  amountCents: number;
  currency: string;
  status: ClientInvoiceStatus;
  paidMethod: InvoicePaidMethod | null;
  paidNote: string | null;
  hostedInvoiceUrl: string | null;
  paidAt: string | null;
  createdAt: string;
};

export type SmsThreadItem = {
  role: "in" | "out";
  text: string;
  at: string;
};

export type StoredSmsDraft = {
  fromDigits: string;
  name: string | null;
  phone: string | null;
  email: string | null;
  service: string | null;
  amountCents: number | null;
  billing: Billing | null;
  address: string | null;
  askedFor: "name" | "phone" | "service" | "price" | null;
  thread: SmsThreadItem[];
  updatedAt: string;
};

export const PROTECH_STRIPE_CUSTOMER_ID = "cus_V8gLsSsiNgy1sT";
export const PROTECH_STRIPE_INVOICE_ID = "in_1U8OyXQRXH1cghMf5NxeURqH";
export const PROTECH_INVOICE_NUMBER = "HAOJMDLO-0001";
export const PROTECH_INVOICE_DESCRIPTION = "Website + systems setup";
export const PROTECH_INVOICE_AMOUNT_CENTS = 150_000;

function normalizeInvoiceStatus(status: string | undefined): ClientInvoiceStatus {
  if (
    status === "draft" ||
    status === "open" ||
    status === "paid" ||
    status === "void" ||
    status === "uncollectible"
  ) {
    return status;
  }
  return "open";
}

function normalizePaidMethod(
  value: string | null | undefined,
): InvoicePaidMethod | null {
  if (value === "card" || value === "cash" || value === "check") return value;
  return null;
}

export function localInvoiceId(quoteId: string) {
  return `local_${quoteId}`;
}

type JsonStore = {
  contacts: ContactSubmission[];
  customers: Customer[];
  quotes: Quote[];
  invoices: ClientInvoice[];
  smsDrafts: Record<string, StoredSmsDraft>;
  settings: Record<string, string>;
};

const emptyStore = (): JsonStore => ({
  contacts: [],
  customers: [],
  quotes: [],
  invoices: [],
  smsDrafts: {},
  settings: {},
});

function postgresUrl() {
  return process.env.POSTGRES_URL || process.env.DATABASE_URL || "";
}

function jsonFile() {
  if (process.env.VERCEL) {
    return "/tmp/gudvector-store.json";
  }
  return path.join(process.cwd(), ".data", "store.json");
}

async function readJson(): Promise<JsonStore> {
  try {
    const raw = await readFile(jsonFile(), "utf8");
    const parsed = { ...emptyStore(), ...JSON.parse(raw) } as JsonStore;
    parsed.quotes = parsed.quotes.map((quote) => ({
      ...quote,
      customerId: quote.customerId ?? null,
      claimToken: quote.claimToken || "",
      serviceAddress: quote.serviceAddress ?? null,
      customerPhone: quote.customerPhone ?? null,
      customerName: quote.customerName ?? null,
      customerEmail: quote.customerEmail ?? null,
      isSeed: Boolean(quote.isSeed),
      source: quote.source === "sms" ? "sms" : "admin",
      smsFromLast4: quote.smsFromLast4 ?? null,
      stripeInvoiceId: quote.stripeInvoiceId ?? null,
      items: quote.items ?? [],
    }));
    parsed.customers = parsed.customers.map((customer) => ({
      ...customer,
      phone: customer.phone ?? null,
      businessName: customer.businessName ?? null,
      role: customer.role === "admin" ? "admin" : "client",
    }));
    parsed.invoices = (parsed.invoices ?? []).map((invoice) => ({
      stripeInvoiceId: invoice.stripeInvoiceId,
      customerId: invoice.customerId ?? null,
      stripeCustomerId: invoice.stripeCustomerId ?? null,
      quoteId: invoice.quoteId ?? null,
      number: invoice.number ?? null,
      description: invoice.description,
      amountCents: Number(invoice.amountCents),
      currency: invoice.currency || "usd",
      status: normalizeInvoiceStatus(invoice.status),
      paidMethod: normalizePaidMethod(invoice.paidMethod),
      paidNote: invoice.paidNote ?? null,
      hostedInvoiceUrl: invoice.hostedInvoiceUrl ?? null,
      paidAt: invoice.paidAt ?? null,
      createdAt: invoice.createdAt,
    }));
    parsed.smsDrafts = parsed.smsDrafts ?? {};
    parsed.settings = parsed.settings ?? {};
    return parsed;
  } catch {
    return emptyStore();
  }
}

async function writeJson(store: JsonStore) {
  const file = jsonFile();
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(store, null, 2));
}

let schemaReady = false;

async function sql() {
  const url = postgresUrl();
  if (!url) return null;
  const client = neon(url, { fullResults: true });
  if (!schemaReady) {
    const statements = [
      `CREATE TABLE IF NOT EXISTS contact_submissions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        business_name TEXT,
        need TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )`,
      `CREATE TABLE IF NOT EXISTS customers (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        business_name TEXT,
        password_hash TEXT NOT NULL,
        stripe_customer_id TEXT,
        phone TEXT,
        role TEXT NOT NULL DEFAULT 'client',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )`,
      `CREATE TABLE IF NOT EXISTS quotes (
        id TEXT PRIMARY KEY,
        customer_id TEXT REFERENCES customers(id),
        claim_token TEXT UNIQUE,
        status TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'usd',
        due_date DATE,
        billing TEXT NOT NULL,
        service_address TEXT,
        customer_phone TEXT,
        customer_name TEXT,
        customer_email TEXT,
        is_seed BOOLEAN NOT NULL DEFAULT FALSE,
        source TEXT NOT NULL DEFAULT 'admin',
        sms_from_last4 TEXT,
        stripe_checkout_session_id TEXT,
        stripe_subscription_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )`,
      `CREATE TABLE IF NOT EXISTS quote_items (
        id TEXT PRIMARY KEY,
        quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        amount_cents INTEGER NOT NULL
      )`,
    ];
    for (const statement of statements) {
      await client.query(statement);
    }
    const alters = [
      `ALTER TABLE quotes ALTER COLUMN customer_id DROP NOT NULL`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS claim_token TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS service_address TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS customer_phone TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS customer_name TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS customer_email TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS is_seed BOOLEAN DEFAULT FALSE`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'admin'`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS sms_from_last4 TEXT`,
      `ALTER TABLE quotes ADD COLUMN IF NOT EXISTS stripe_invoice_id TEXT`,
      `ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT`,
      `ALTER TABLE customers ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'client'`,
      `ALTER TABLE customers ADD COLUMN IF NOT EXISTS business_name TEXT`,
      `CREATE UNIQUE INDEX IF NOT EXISTS quotes_claim_token_uidx ON quotes (claim_token)`,
      `CREATE TABLE IF NOT EXISTS client_invoices (
        stripe_invoice_id TEXT PRIMARY KEY,
        customer_id TEXT REFERENCES customers(id),
        stripe_customer_id TEXT,
        quote_id TEXT,
        number TEXT,
        description TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'usd',
        status TEXT NOT NULL,
        paid_method TEXT,
        paid_note TEXT,
        hosted_invoice_url TEXT,
        paid_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )`,
      `ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS quote_id TEXT`,
      `ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS paid_method TEXT`,
      `ALTER TABLE client_invoices ADD COLUMN IF NOT EXISTS paid_note TEXT`,
      `CREATE TABLE IF NOT EXISTS sms_drafts (
        from_digits TEXT PRIMARY KEY,
        name TEXT,
        phone TEXT,
        email TEXT,
        service TEXT,
        amount_cents INTEGER,
        billing TEXT,
        address TEXT,
        asked_for TEXT,
        thread JSONB NOT NULL DEFAULT '[]'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )`,
      `CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      )`,
    ];
    for (const statement of alters) {
      try {
        await client.query(statement);
      } catch {
        // Older Postgres variants may already have the shape we need.
      }
    }
    schemaReady = true;
  }
  return client;
}

function mapQuote(
  row: Record<string, unknown>,
  items: QuoteItem[] = [],
): Quote {
  return {
    id: String(row.id),
    customerId: row.customer_id ? String(row.customer_id) : null,
    claimToken: row.claim_token ? String(row.claim_token) : "",
    status: row.status as QuoteStatus,
    amountCents: Number(row.amount_cents),
    currency: String(row.currency),
    dueDate: row.due_date ? String(row.due_date).slice(0, 10) : null,
    billing: row.billing as Billing,
    serviceAddress: row.service_address ? String(row.service_address) : null,
    customerPhone: row.customer_phone ? String(row.customer_phone) : null,
    customerName: row.customer_name ? String(row.customer_name) : null,
    customerEmail: row.customer_email ? String(row.customer_email) : null,
    isSeed: Boolean(row.is_seed),
    source: row.source === "sms" ? "sms" : "admin",
    smsFromLast4: row.sms_from_last4 ? String(row.sms_from_last4) : null,
    stripeCheckoutSessionId: row.stripe_checkout_session_id
      ? String(row.stripe_checkout_session_id)
      : null,
    stripeSubscriptionId: row.stripe_subscription_id
      ? String(row.stripe_subscription_id)
      : null,
    stripeInvoiceId: row.stripe_invoice_id
      ? String(row.stripe_invoice_id)
      : null,
    createdAt: new Date(String(row.created_at)).toISOString(),
    items,
  };
}

function mapCustomer(row: Record<string, unknown>): Customer {
  return {
    id: String(row.id),
    email: String(row.email),
    name: String(row.name),
    businessName: row.business_name ? String(row.business_name) : null,
    passwordHash: String(row.password_hash),
    stripeCustomerId: row.stripe_customer_id
      ? String(row.stripe_customer_id)
      : null,
    phone: row.phone ? String(row.phone) : null,
    role: row.role === "admin" ? "admin" : "client",
    createdAt: new Date(String(row.created_at)).toISOString(),
  };
}

function mapClientInvoice(row: Record<string, unknown>): ClientInvoice {
  return {
    stripeInvoiceId: String(row.stripe_invoice_id),
    customerId: row.customer_id ? String(row.customer_id) : null,
    stripeCustomerId: row.stripe_customer_id
      ? String(row.stripe_customer_id)
      : null,
    quoteId: row.quote_id ? String(row.quote_id) : null,
    number: row.number ? String(row.number) : null,
    description: String(row.description),
    amountCents: Number(row.amount_cents),
    currency: String(row.currency || "usd"),
    status: normalizeInvoiceStatus(String(row.status)),
    paidMethod: normalizePaidMethod(
      row.paid_method ? String(row.paid_method) : null,
    ),
    paidNote: row.paid_note ? String(row.paid_note) : null,
    hostedInvoiceUrl: row.hosted_invoice_url
      ? String(row.hosted_invoice_url)
      : null,
    paidAt: row.paid_at ? new Date(String(row.paid_at)).toISOString() : null,
    createdAt: new Date(String(row.created_at)).toISOString(),
  };
}

function requireWriteStore() {
  if (postgresUrl()) return;
  if (process.env.VERCEL) {
    throw new Error("POSTGRES_URL is required to store data in production.");
  }
}

export function hasDurableStore() {
  return Boolean(postgresUrl());
}

export async function saveContact(
  input: Omit<ContactSubmission, "id" | "createdAt">,
): Promise<ContactSubmission> {
  requireWriteStore();

  const record: ContactSubmission = {
    id: createId("msg"),
    createdAt: new Date().toISOString(),
    ...input,
  };

  const client = await sql();
  if (client) {
    await client.query(
      `INSERT INTO contact_submissions (id, name, email, business_name, need, message, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7)`,
      [
        record.id,
        record.name,
        record.email,
        record.businessName,
        record.need,
        record.message,
        record.createdAt,
      ],
    );
    return record;
  }

  const store = await readJson();
  store.contacts.unshift(record);
  await writeJson(store);
  return record;
}

export async function getCustomerByEmail(email: string) {
  const normalized = email.trim().toLowerCase();
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM customers WHERE lower(email) = $1 LIMIT 1`,
      [normalized],
    );
    return rows[0] ? mapCustomer(rows[0] as Record<string, unknown>) : null;
  }
  const store = await readJson();
  return (
    store.customers.find((c) => c.email.toLowerCase() === normalized) ?? null
  );
}

export function isPendingCustomer(customer: Customer) {
  return !customer.passwordHash.startsWith("$2");
}

export async function getCustomerByPhone(phone: string) {
  const e164 = toE164(phone);
  if (!e164) return null;
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM customers
       WHERE right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 10) = $1
       LIMIT 1`,
      [phoneDigits(e164)],
    );
    return rows[0] ? mapCustomer(rows[0] as Record<string, unknown>) : null;
  }
  const store = await readJson();
  return store.customers.find((customer) => samePhone(customer.phone, e164)) ?? null;
}

export async function getCustomerById(id: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(`SELECT * FROM customers WHERE id = $1`, [
      id,
    ]);
    return rows[0] ? mapCustomer(rows[0] as Record<string, unknown>) : null;
  }
  const store = await readJson();
  return store.customers.find((c) => c.id === id) ?? null;
}

export async function createCustomer(input: {
  email: string;
  name: string;
  passwordHash: string;
  phone?: string | null;
  businessName?: string | null;
  role?: PortalRole;
}) {
  requireWriteStore();

  const customer: Customer = {
    id: createId("cus"),
    email: input.email.trim().toLowerCase(),
    name: input.name.trim(),
    businessName: input.businessName?.trim() || null,
    passwordHash: input.passwordHash,
    stripeCustomerId: null,
    phone: input.phone?.trim() || null,
    role: input.role === "admin" ? "admin" : "client",
    createdAt: new Date().toISOString(),
  };

  const client = await sql();
  if (client) {
    await client.query(
      `INSERT INTO customers (id, email, name, business_name, password_hash, phone, role, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [
        customer.id,
        customer.email,
        customer.name,
        customer.businessName,
        customer.passwordHash,
        customer.phone,
        customer.role,
        customer.createdAt,
      ],
    );
    return customer;
  }

  const store = await readJson();
  store.customers.push(customer);
  await writeJson(store);
  return customer;
}

export async function setCustomerStripeId(
  customerId: string,
  stripeCustomerId: string,
) {
  const client = await sql();
  if (client) {
    await client.query(
      `UPDATE customers SET stripe_customer_id = $1 WHERE id = $2`,
      [stripeCustomerId, customerId],
    );
    return;
  }
  const store = await readJson();
  const customer = store.customers.find((c) => c.id === customerId);
  if (customer) {
    customer.stripeCustomerId = stripeCustomerId;
    await writeJson(store);
  }
}

export async function getCustomerByStripeId(stripeCustomerId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM customers WHERE stripe_customer_id = $1 LIMIT 1`,
      [stripeCustomerId],
    );
    return rows[0] ? mapCustomer(rows[0] as Record<string, unknown>) : null;
  }
  const store = await readJson();
  return (
    store.customers.find((c) => c.stripeCustomerId === stripeCustomerId) ?? null
  );
}

async function attachItems(quotes: Quote[]): Promise<Quote[]> {
  if (quotes.length === 0) return quotes;
  const client = await sql();
  if (client) {
    const ids = quotes.map((q) => q.id);
    const { rows } = await client.query(
      `SELECT * FROM quote_items WHERE quote_id = ANY($1::text[])`,
      [ids],
    );
    const byQuote = new Map<string, QuoteItem[]>();
    for (const row of rows) {
      const item: QuoteItem = {
        id: String(row.id),
        quoteId: String(row.quote_id),
        description: String(row.description),
        quantity: Number(row.quantity),
        amountCents: Number(row.amount_cents),
      };
      const list = byQuote.get(item.quoteId) ?? [];
      list.push(item);
      byQuote.set(item.quoteId, list);
    }
    return quotes.map((quote) => ({
      ...quote,
      items: byQuote.get(quote.id) ?? [],
    }));
  }
  return quotes;
}

export async function listQuotesForCustomer(customerId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes WHERE customer_id = $1 ORDER BY created_at DESC`,
      [customerId],
    );
    const quotes = rows.map((row) => mapQuote(row as Record<string, unknown>));
    return attachItems(quotes);
  }
  const store = await readJson();
  return store.quotes
    .filter((q) => q.customerId === customerId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function getQuoteById(id: string): Promise<QuoteWithCustomer | null> {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(`SELECT * FROM quotes WHERE id = $1`, [
      id,
    ]);
    if (!rows[0]) return null;
    const [quote] = await attachItems([
      mapQuote(rows[0] as Record<string, unknown>),
    ]);
    const customer = quote.customerId
      ? await getCustomerById(quote.customerId)
      : null;
    return { ...quote, customer };
  }
  const store = await readJson();
  const quote = store.quotes.find((q) => q.id === id);
  if (!quote) return null;
  const customer = quote.customerId
    ? (store.customers.find((c) => c.id === quote.customerId) ?? null)
    : null;
  return { ...quote, customer };
}

export async function getQuoteByToken(
  token: string,
): Promise<QuoteWithCustomer | null> {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes WHERE claim_token = $1 LIMIT 1`,
      [token],
    );
    if (!rows[0]) return null;
    const [quote] = await attachItems([
      mapQuote(rows[0] as Record<string, unknown>),
    ]);
    const customer = quote.customerId
      ? await getCustomerById(quote.customerId)
      : null;
    return { ...quote, customer };
  }
  const store = await readJson();
  const quote = store.quotes.find((q) => q.claimToken === token);
  if (!quote) return null;
  const customer = quote.customerId
    ? (store.customers.find((c) => c.id === quote.customerId) ?? null)
    : null;
  return { ...quote, customer };
}

export async function createQuote(input: {
  id?: string;
  customerId?: string | null;
  amountCents: number;
  dueDate?: string | null;
  billing: Billing;
  items: { description: string; quantity: number; amountCents: number }[];
  serviceAddress?: string | null;
  customerPhone?: string | null;
  customerName?: string | null;
  customerEmail?: string | null;
  status?: QuoteStatus;
  source?: QuoteSource;
  smsFromLast4?: string | null;
  isSeed?: boolean;
  stripeInvoiceId?: string | null;
}) {
  requireWriteStore();

  const quote: Quote = {
    id: input.id ?? createId("quo"),
    customerId: input.customerId ?? null,
    claimToken: createClaimToken(),
    status: input.status ?? "sent",
    amountCents: input.amountCents,
    currency: "usd",
    dueDate: input.dueDate ?? null,
    billing: input.billing,
    serviceAddress: input.serviceAddress ?? null,
    customerPhone: input.customerPhone ?? null,
    customerName: input.customerName ?? null,
    customerEmail: input.customerEmail?.trim().toLowerCase() || null,
    isSeed: Boolean(input.isSeed),
    source: input.source ?? "admin",
    smsFromLast4: input.smsFromLast4 ?? null,
    stripeCheckoutSessionId: null,
    stripeSubscriptionId: null,
    stripeInvoiceId: input.stripeInvoiceId ?? null,
    createdAt: new Date().toISOString(),
    items: input.items.map((item) => ({
      id: createId("itm"),
      quoteId: "",
      description: item.description,
      quantity: item.quantity,
      amountCents: item.amountCents,
    })),
  };
  quote.items = quote.items.map((item) => ({ ...item, quoteId: quote.id }));

  const client = await sql();
  if (client) {
    await client.query(
      `INSERT INTO quotes (id, customer_id, claim_token, status, amount_cents, currency, due_date, billing, service_address, customer_phone, customer_name, customer_email, is_seed, source, sms_from_last4, stripe_invoice_id, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)`,
      [
        quote.id,
        quote.customerId,
        quote.claimToken,
        quote.status,
        quote.amountCents,
        quote.currency,
        quote.dueDate,
        quote.billing,
        quote.serviceAddress,
        quote.customerPhone,
        quote.customerName,
        quote.customerEmail,
        quote.isSeed,
        quote.source,
        quote.smsFromLast4,
        quote.stripeInvoiceId,
        quote.createdAt,
      ],
    );
    for (const item of quote.items) {
      await client.query(
        `INSERT INTO quote_items (id, quote_id, description, quantity, amount_cents)
         VALUES ($1, $2, $3, $4, $5)`,
        [item.id, quote.id, item.description, item.quantity, item.amountCents],
      );
    }
    return quote;
  }

  const store = await readJson();
  store.quotes.unshift(quote);
  await writeJson(store);
  return quote;
}

export async function updateQuote(
  id: string,
  patch: Partial<
    Pick<
      Quote,
      | "status"
      | "stripeCheckoutSessionId"
      | "stripeSubscriptionId"
      | "stripeInvoiceId"
    >
  >,
) {
  requireWriteStore();

  const client = await sql();
  if (client) {
    const sets: string[] = [];
    const values: unknown[] = [];
    if (patch.status) {
      sets.push(`status = $${sets.length + 1}`);
      values.push(patch.status);
    }
    if (patch.stripeCheckoutSessionId !== undefined) {
      sets.push(`stripe_checkout_session_id = $${sets.length + 1}`);
      values.push(patch.stripeCheckoutSessionId);
    }
    if (patch.stripeSubscriptionId !== undefined) {
      sets.push(`stripe_subscription_id = $${sets.length + 1}`);
      values.push(patch.stripeSubscriptionId);
    }
    if (patch.stripeInvoiceId !== undefined) {
      sets.push(`stripe_invoice_id = $${sets.length + 1}`);
      values.push(patch.stripeInvoiceId);
    }
    if (sets.length === 0) return;
    values.push(id);
    await client.query(
      `UPDATE quotes SET ${sets.join(", ")} WHERE id = $${values.length}`,
      values,
    );
    return;
  }

  const store = await readJson();
  const quote = store.quotes.find((q) => q.id === id);
  if (!quote) return;
  Object.assign(quote, patch);
  await writeJson(store);
}

export async function getQuoteByCheckoutSession(sessionId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes WHERE stripe_checkout_session_id = $1 LIMIT 1`,
      [sessionId],
    );
    if (!rows[0]) return null;
    const [quote] = await attachItems([
      mapQuote(rows[0] as Record<string, unknown>),
    ]);
    return quote;
  }
  const store = await readJson();
  return (
    store.quotes.find((q) => q.stripeCheckoutSessionId === sessionId) ?? null
  );
}

export async function getQuoteBySubscription(subscriptionId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes WHERE stripe_subscription_id = $1 LIMIT 1`,
      [subscriptionId],
    );
    if (!rows[0]) return null;
    const [quote] = await attachItems([
      mapQuote(rows[0] as Record<string, unknown>),
    ]);
    return quote;
  }
  const store = await readJson();
  return (
    store.quotes.find((q) => q.stripeSubscriptionId === subscriptionId) ?? null
  );
}

export async function getQuoteByStripeInvoiceId(stripeInvoiceId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes WHERE stripe_invoice_id = $1 LIMIT 1`,
      [stripeInvoiceId],
    );
    if (!rows[0]) return null;
    const [quote] = await attachItems([
      mapQuote(rows[0] as Record<string, unknown>),
    ]);
    return quote;
  }
  const store = await readJson();
  return (
    store.quotes.find((q) => q.stripeInvoiceId === stripeInvoiceId) ?? null
  );
}

export async function listCustomers() {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM customers ORDER BY created_at DESC`,
    );
    return rows.map((row) => mapCustomer(row as Record<string, unknown>));
  }
  const store = await readJson();
  return [...store.customers].sort((a, b) =>
    b.createdAt.localeCompare(a.createdAt),
  );
}

export async function listRecentQuotes(limit = 40) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM quotes ORDER BY created_at DESC LIMIT $1`,
      [limit],
    );
    return attachItems(
      rows.map((row) => mapQuote(row as Record<string, unknown>)),
    );
  }
  const store = await readJson();
  return [...store.quotes]
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .slice(0, limit);
}

export async function updateCustomer(
  id: string,
  patch: {
    name?: string;
    businessName?: string | null;
    phone?: string | null;
    role?: PortalRole;
    email?: string;
    passwordHash?: string;
  },
) {
  requireWriteStore();
  const client = await sql();
  if (client) {
    const sets: string[] = [];
    const values: unknown[] = [];
    if (patch.name !== undefined) {
      sets.push(`name = $${sets.length + 1}`);
      values.push(patch.name);
    }
    if (patch.businessName !== undefined) {
      sets.push(`business_name = $${sets.length + 1}`);
      values.push(patch.businessName);
    }
    if (patch.phone !== undefined) {
      sets.push(`phone = $${sets.length + 1}`);
      values.push(patch.phone);
    }
    if (patch.role !== undefined) {
      sets.push(`role = $${sets.length + 1}`);
      values.push(patch.role);
    }
    if (patch.email !== undefined) {
      sets.push(`email = $${sets.length + 1}`);
      values.push(patch.email.trim().toLowerCase());
    }
    if (patch.passwordHash !== undefined) {
      sets.push(`password_hash = $${sets.length + 1}`);
      values.push(patch.passwordHash);
    }
    if (sets.length === 0) return;
    values.push(id);
    await client.query(
      `UPDATE customers SET ${sets.join(", ")} WHERE id = $${values.length}`,
      values,
    );
    return getCustomerById(id);
  }
  const store = await readJson();
  const customer = store.customers.find((c) => c.id === id);
  if (!customer) return null;
  if (patch.name !== undefined) customer.name = patch.name;
  if (patch.businessName !== undefined) customer.businessName = patch.businessName;
  if (patch.phone !== undefined) customer.phone = patch.phone;
  if (patch.role !== undefined) customer.role = patch.role;
  if (patch.email !== undefined) customer.email = patch.email.trim().toLowerCase();
  if (patch.passwordHash !== undefined) customer.passwordHash = patch.passwordHash;
  await writeJson(store);
  return customer;
}

export async function findOrCreateEndUser(input: {
  name: string;
  email?: string | null;
  phone?: string | null;
}) {
  let email = input.email?.trim().toLowerCase() || null;
  const phone = toE164(input.phone);
  if (email) {
    const existing = await getCustomerByEmail(email);
    if (existing?.role === "admin") {
      email = null;
    } else if (existing) {
      const patch: { name?: string; phone?: string | null } = {};
      if (input.name && (existing.name === "Account" || isPendingCustomer(existing))) {
        patch.name = input.name;
      }
      if (phone && !existing.phone) patch.phone = phone;
      if (Object.keys(patch).length) await updateCustomer(existing.id, patch);
      return (await getCustomerById(existing.id)) ?? existing;
    }
  }
  if (phone) {
    const existing = await getCustomerByPhone(phone);
    if (existing && existing.role !== "admin") {
      const patch: { name?: string; email?: string; phone?: string | null } = {};
      if (input.name) patch.name = input.name;
      if (phone && existing.phone !== phone) patch.phone = phone;
      if (email && isPendingEmail(existing.email)) patch.email = email;
      if (Object.keys(patch).length) await updateCustomer(existing.id, patch);
      return (await getCustomerById(existing.id)) ?? existing;
    }
  }
  if (!email && !phone) return null;
  return createCustomer({
    email: email || pendingEmailForPhone(phone || "0000000000"),
    name: input.name.trim() || "Customer",
    passwordHash: PENDING_PASSWORD_HASH,
    phone,
    role: "client",
  });
}

export async function claimQuoteForCustomer(
  token: string,
  customer: Customer,
) {
  const quote = await getQuoteByToken(token);
  if (!quote) return null;
  if (quote.customerId && quote.customerId !== customer.id) {
    const assigned = await getCustomerById(quote.customerId);
    const samePerson = Boolean(
      (assigned &&
        assigned.role !== "admin" &&
        ((customer.email &&
          assigned.email.toLowerCase() === customer.email.toLowerCase()) ||
          samePhone(assigned.phone, customer.phone))) ||
        (quote.customerEmail &&
          customer.email &&
          quote.customerEmail === customer.email) ||
        samePhone(quote.customerPhone, customer.phone),
    );
    if (!samePerson) return quote;
  }
  await attachQuoteToCustomer(quote.id, customer.id, {
    customerPhone: customer.phone,
  });
  await updateQuoteDetails(quote.id, {
    customerName: customer.name,
    customerEmail: isPendingEmail(customer.email) ? null : customer.email,
    customerPhone: customer.phone,
  });
  return getQuoteById(quote.id);
}

export async function attachQuoteToCustomer(
  quoteId: string,
  customerId: string,
  extra?: { customerPhone?: string | null },
) {
  requireWriteStore();
  const client = await sql();
  if (client) {
    await client.query(
      `UPDATE quotes SET customer_id = $1, customer_phone = COALESCE($2, customer_phone) WHERE id = $3`,
      [customerId, extra?.customerPhone ?? null, quoteId],
    );
    return;
  }
  const store = await readJson();
  const quote = store.quotes.find((q) => q.id === quoteId);
  if (!quote) return;
  quote.customerId = customerId;
  if (extra?.customerPhone) quote.customerPhone = extra.customerPhone;
  await writeJson(store);
}

export async function updateQuoteDetails(
  id: string,
  patch: {
    customerPhone?: string | null;
    customerName?: string | null;
    customerEmail?: string | null;
  },
) {
  requireWriteStore();
  const client = await sql();
  if (client) {
    await client.query(
      `UPDATE quotes SET
        customer_phone = COALESCE($1, customer_phone),
        customer_name = COALESCE($2, customer_name),
        customer_email = COALESCE($3, customer_email)
      WHERE id = $4`,
      [
        patch.customerPhone ?? null,
        patch.customerName ?? null,
        patch.customerEmail?.trim().toLowerCase() || null,
        id,
      ],
    );
    return;
  }
  const store = await readJson();
  const quote = store.quotes.find((q) => q.id === id);
  if (!quote) return;
  if (patch.customerPhone) quote.customerPhone = patch.customerPhone;
  if (patch.customerName) quote.customerName = patch.customerName;
  if (patch.customerEmail) {
    quote.customerEmail = patch.customerEmail.trim().toLowerCase();
  }
  await writeJson(store);
}

export async function deleteQuote(id: string) {
  requireWriteStore();
  const client = await sql();
  if (client) {
    await client.query(`DELETE FROM quote_items WHERE quote_id = $1`, [id]);
    await client.query(`DELETE FROM quotes WHERE id = $1`, [id]);
    return;
  }
  const store = await readJson();
  store.quotes = store.quotes.filter((quote) => quote.id !== id);
  await writeJson(store);
}

export async function saveQuoteEdits(
  id: string,
  patch: {
    customerName: string;
    customerPhone: string;
    customerEmail: string;
    serviceAddress: string;
    service: string;
    amountCents: number;
    billing?: Billing;
  },
) {
  requireWriteStore();
  const existing = await getQuoteById(id);
  if (!existing) return null;
  const billing = patch.billing ?? existing.billing;

  const items = [
    {
      id: existing.items[0]?.id ?? createId("itm"),
      quoteId: id,
      description: patch.service.trim(),
      quantity: 1,
      amountCents: patch.amountCents,
    },
  ];

  const client = await sql();
  if (client) {
    await client.query(
      `UPDATE quotes SET
        customer_name = $1,
        customer_phone = $2,
        customer_email = $3,
        service_address = $4,
        amount_cents = $5,
        billing = $6
      WHERE id = $7`,
      [
        patch.customerName.trim() || null,
        patch.customerPhone.trim() || null,
        patch.customerEmail.trim().toLowerCase() || null,
        patch.serviceAddress.trim() || null,
        patch.amountCents,
        billing,
        id,
      ],
    );
    await client.query(`DELETE FROM quote_items WHERE quote_id = $1`, [id]);
    await client.query(
      `INSERT INTO quote_items (id, quote_id, description, quantity, amount_cents)
       VALUES ($1, $2, $3, $4, $5)`,
      [items[0].id, id, items[0].description, items[0].quantity, items[0].amountCents],
    );
    return getQuoteById(id);
  }

  const store = await readJson();
  const quote = store.quotes.find((q) => q.id === id);
  if (!quote) return null;
  quote.customerName = patch.customerName.trim() || null;
  quote.customerPhone = patch.customerPhone.trim() || null;
  quote.customerEmail = patch.customerEmail.trim().toLowerCase() || null;
  quote.serviceAddress = patch.serviceAddress.trim() || null;
  quote.amountCents = patch.amountCents;
  quote.billing = billing;
  quote.items = items;
  await writeJson(store);
  return { ...quote, customer: existing.customer };
}

export async function expireSentQuotes(customerId?: string) {
  const quotes = customerId
    ? await listQuotesForCustomer(customerId)
    : await listRecentQuotes(500);
  for (const quote of quotes) {
    if (quote.isSeed) continue;
    if (isExpiredSent(quote)) {
      await deleteQuote(quote.id);
    }
  }
}

export async function listQuotesForShop() {
  await expireSentQuotes();
  return listRecentQuotes(200);
}

export async function listQuotesForEndUser(customer: Customer) {
  await expireSentQuotes(customer.id);
  const quotes = await listQuotesForCustomer(customer.id);
  const extra = (await listRecentQuotes(200)).filter((quote) => {
    if (quote.customerId === customer.id) return false;
    if (
      customer.email &&
      !isPendingEmail(customer.email) &&
      quote.customerEmail === customer.email
    ) {
      return true;
    }
    return samePhone(quote.customerPhone, customer.phone);
  });
  const byId = new Map<string, Quote>();
  for (const quote of [...quotes, ...extra]) byId.set(quote.id, quote);
  return [...byId.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function listClientAccounts(customers: Customer[]) {
  return customers.filter((customer) => customer.role !== "admin");
}

export function invoicePortalLabel(status: ClientInvoiceStatus) {
  if (status === "paid") return "Paid";
  if (status === "open" || status === "draft") return "Due";
  if (status === "void") return "Void";
  return "Uncollectible";
}

export function isPortalInvoice(invoice: ClientInvoice) {
  return invoice.status === "open" || invoice.status === "paid";
}

export async function getInvoiceByStripeId(stripeInvoiceId: string) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM client_invoices WHERE stripe_invoice_id = $1 LIMIT 1`,
      [stripeInvoiceId],
    );
    return rows[0]
      ? mapClientInvoice(rows[0] as Record<string, unknown>)
      : null;
  }
  const store = await readJson();
  return (
    store.invoices.find((invoice) => invoice.stripeInvoiceId === stripeInvoiceId) ??
    null
  );
}

export async function listAllInvoices() {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM client_invoices ORDER BY created_at DESC`,
    );
    return rows.map((row) => mapClientInvoice(row as Record<string, unknown>));
  }
  const store = await readJson();
  return [...store.invoices].sort((a, b) =>
    b.createdAt.localeCompare(a.createdAt),
  );
}

export async function listInvoicesForCustomer(customer: Customer) {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM client_invoices
       WHERE customer_id = $1
          OR ($2::text IS NOT NULL AND stripe_customer_id = $2)
       ORDER BY created_at DESC`,
      [customer.id, customer.stripeCustomerId],
    );
    return rows.map((row) => mapClientInvoice(row as Record<string, unknown>));
  }
  const store = await readJson();
  return store.invoices
    .filter(
      (invoice) =>
        invoice.customerId === customer.id ||
        (customer.stripeCustomerId &&
          invoice.stripeCustomerId === customer.stripeCustomerId),
    )
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export async function upsertClientInvoice(
  input: Omit<ClientInvoice, "createdAt"> & { createdAt?: string },
) {
  const existing = await getInvoiceByStripeId(input.stripeInvoiceId);
  const status = input.status;
  const paidMethod =
    input.paidMethod !== undefined
      ? input.paidMethod
      : existing?.paidMethod ?? null;
  const record: ClientInvoice = {
    stripeInvoiceId: input.stripeInvoiceId,
    customerId: input.customerId ?? existing?.customerId ?? null,
    stripeCustomerId:
      input.stripeCustomerId ?? existing?.stripeCustomerId ?? null,
    quoteId: input.quoteId ?? existing?.quoteId ?? null,
    number: input.number ?? existing?.number ?? null,
    description: input.description,
    amountCents: input.amountCents,
    currency: input.currency || "usd",
    status,
    paidMethod: status === "paid" ? paidMethod : null,
    paidNote:
      input.paidNote !== undefined
        ? input.paidNote
        : existing?.paidNote ?? null,
    hostedInvoiceUrl:
      input.hostedInvoiceUrl ?? existing?.hostedInvoiceUrl ?? null,
    paidAt:
      status === "paid"
        ? (input.paidAt ?? existing?.paidAt ?? new Date().toISOString())
        : null,
    createdAt: existing?.createdAt ?? input.createdAt ?? new Date().toISOString(),
  };

  const db = await sql();
  if (db) {
    await db.query(
      `INSERT INTO client_invoices (
        stripe_invoice_id, customer_id, stripe_customer_id, quote_id, number, description,
        amount_cents, currency, status, paid_method, paid_note, hosted_invoice_url, paid_at, created_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
      ON CONFLICT (stripe_invoice_id) DO UPDATE SET
        customer_id = COALESCE(EXCLUDED.customer_id, client_invoices.customer_id),
        stripe_customer_id = COALESCE(EXCLUDED.stripe_customer_id, client_invoices.stripe_customer_id),
        quote_id = COALESCE(EXCLUDED.quote_id, client_invoices.quote_id),
        number = COALESCE(EXCLUDED.number, client_invoices.number),
        description = EXCLUDED.description,
        amount_cents = EXCLUDED.amount_cents,
        currency = EXCLUDED.currency,
        status = EXCLUDED.status,
        paid_method = EXCLUDED.paid_method,
        paid_note = EXCLUDED.paid_note,
        hosted_invoice_url = COALESCE(EXCLUDED.hosted_invoice_url, client_invoices.hosted_invoice_url),
        paid_at = EXCLUDED.paid_at`,
      [
        record.stripeInvoiceId,
        record.customerId,
        record.stripeCustomerId,
        record.quoteId,
        record.number,
        record.description,
        record.amountCents,
        record.currency,
        record.status,
        record.paidMethod,
        record.paidNote,
        record.hostedInvoiceUrl,
        record.paidAt,
        record.createdAt,
      ],
    );
    return getInvoiceByStripeId(record.stripeInvoiceId);
  }

  const store = await readJson();
  const index = store.invoices.findIndex(
    (invoice) => invoice.stripeInvoiceId === record.stripeInvoiceId,
  );
  if (index >= 0) store.invoices[index] = record;
  else store.invoices.push(record);
  await writeJson(store);
  return record;
}

export async function ensureInvoiceForQuote(quote: Quote) {
  const stripeInvoiceId = quote.stripeInvoiceId || localInvoiceId(quote.id);
  const existing = await getInvoiceByStripeId(stripeInvoiceId);
  if (existing) {
    const needsQuote = !existing.quoteId;
    const needsCustomer = !existing.customerId && Boolean(quote.customerId);
    if (needsQuote || needsCustomer) {
      return upsertClientInvoice({
        ...existing,
        quoteId: existing.quoteId || quote.id,
        customerId: existing.customerId || quote.customerId,
      });
    }
    return existing;
  }

  const paid = quote.status === "paid" || quote.status === "acknowledged";
  return upsertClientInvoice({
    stripeInvoiceId,
    customerId: quote.customerId,
    stripeCustomerId: null,
    quoteId: quote.id,
    number: null,
    description: quote.items[0]?.description || "Service",
    amountCents: quote.amountCents,
    currency: quote.currency,
    status: paid ? "paid" : "open",
    paidMethod: paid ? "card" : null,
    paidNote: null,
    hostedInvoiceUrl: null,
    paidAt: paid ? new Date().toISOString() : null,
  });
}

async function markLinkedQuotePaid(invoice: ClientInvoice) {
  const quote =
    (await getQuoteByStripeInvoiceId(invoice.stripeInvoiceId)) ||
    (invoice.quoteId ? await getQuoteById(invoice.quoteId) : null);
  if (!quote) return;
  if (quote.status === "canceled") return;
  await updateQuote(quote.id, {
    status: "paid",
    stripeInvoiceId: quote.stripeInvoiceId || invoice.stripeInvoiceId,
  });
}

export async function markInvoicePaidByCard(
  stripeInvoiceId: string,
  paidAt?: string | null,
) {
  const existing = await getInvoiceByStripeId(stripeInvoiceId);
  if (!existing) return null;
  const record = await upsertClientInvoice({
    ...existing,
    status: "paid",
    paidMethod: "card",
    paidAt: paidAt ?? existing.paidAt ?? new Date().toISOString(),
  });
  if (record) await markLinkedQuotePaid(record);
  return record;
}

export async function markInvoicePaidInPerson(
  stripeInvoiceId: string,
  input: {
    method: "cash" | "check";
    note?: string | null;
    paidAt?: string | null;
  },
) {
  const existing = await getInvoiceByStripeId(stripeInvoiceId);
  if (!existing) return null;
  if (existing.status === "paid") return existing;
  const record = await upsertClientInvoice({
    ...existing,
    status: "paid",
    paidMethod: input.method,
    paidNote: input.note?.trim() || null,
    paidAt: input.paidAt || new Date().toISOString(),
  });
  if (record) await markLinkedQuotePaid(record);
  return record;
}

export async function markInvoicePaid(stripeInvoiceId: string) {
  return markInvoicePaidByCard(stripeInvoiceId);
}

export async function listInvoicesForShop() {
  const quotes = await listRecentQuotes(200);
  for (const quote of quotes) {
    if (
      quote.stripeInvoiceId ||
      quote.status === "accepted" ||
      quote.status === "paid" ||
      quote.status === "paused" ||
      quote.status === "acknowledged"
    ) {
      await ensureInvoiceForQuote(quote);
    }
  }
  return (await listAllInvoices()).filter(isPortalInvoice);
}

export async function seedProtechPayment(customerId: string) {
  await setCustomerStripeId(customerId, PROTECH_STRIPE_CUSTOMER_ID);
  const existing = await getInvoiceByStripeId(PROTECH_STRIPE_INVOICE_ID);
  if (existing) {
    if (
      existing.customerId !== customerId ||
      existing.status === "paid" && !existing.paidMethod
    ) {
      await upsertClientInvoice({
        ...existing,
        customerId,
        stripeCustomerId: PROTECH_STRIPE_CUSTOMER_ID,
        paidMethod: existing.status === "paid" ? existing.paidMethod || "card" : null,
      });
    }
    return;
  }
  await upsertClientInvoice({
    stripeInvoiceId: PROTECH_STRIPE_INVOICE_ID,
    customerId,
    stripeCustomerId: PROTECH_STRIPE_CUSTOMER_ID,
    quoteId: null,
    number: PROTECH_INVOICE_NUMBER,
    description: PROTECH_INVOICE_DESCRIPTION,
    amountCents: PROTECH_INVOICE_AMOUNT_CENTS,
    currency: "usd",
    status: "open",
    paidMethod: null,
    paidNote: null,
    hostedInvoiceUrl: null,
    paidAt: null,
  });
}

export async function removeShopDeskSeedQuotes(customerId: string) {
  const existing = await listQuotesForCustomer(customerId);
  for (const quote of existing) {
    const deskName =
      quote.customerName === "Dana Ruiz" || quote.customerName === "Chris Lang";
    if (quote.isSeed && deskName) {
      await deleteQuote(quote.id);
    }
  }
}

export async function seedGlenQuote(customerId: string) {
  await seedProtechPayment(customerId);
  const existing = await listQuotesForCustomer(customerId);
  const related = existing.filter(
    (quote) =>
      quote.stripeInvoiceId === PROTECH_STRIPE_INVOICE_ID ||
      (quote.items[0]?.description === PROTECH_INVOICE_DESCRIPTION &&
        quote.amountCents === PROTECH_INVOICE_AMOUNT_CENTS),
  );
  const open = related.find(
    (quote) => quote.status === "sent" || quote.status === "draft",
  );
  if (open) {
    if (!open.stripeInvoiceId) {
      await updateQuote(open.id, { stripeInvoiceId: PROTECH_STRIPE_INVOICE_ID });
    }
    await ensureInvoiceForQuote({
      ...open,
      stripeInvoiceId: open.stripeInvoiceId || PROTECH_STRIPE_INVOICE_ID,
    });
    return open;
  }
  const inFlight = related.find((quote) =>
    ["accepted", "paid", "paused", "canceled", "acknowledged"].includes(
      quote.status,
    ),
  );
  if (inFlight) {
    await ensureInvoiceForQuote(inFlight);
    return inFlight;
  }
  const created = await createQuote({
    customerId,
    amountCents: PROTECH_INVOICE_AMOUNT_CENTS,
    billing: "one_time",
    status: "sent",
    isSeed: true,
    customerName: "ProTech",
    customerEmail: "glen@protech-cal.com",
    stripeInvoiceId: PROTECH_STRIPE_INVOICE_ID,
    items: [
      {
        description: PROTECH_INVOICE_DESCRIPTION,
        quantity: 1,
        amountCents: PROTECH_INVOICE_AMOUNT_CENTS,
      },
    ],
  });
  const invoice = await getInvoiceByStripeId(PROTECH_STRIPE_INVOICE_ID);
  if (invoice) {
    await upsertClientInvoice({
      ...invoice,
      quoteId: created.id,
      customerId,
    });
  }
  return created;
}

const SMS_DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
const SMS_THREAD_MAX = 8;

function normalizeAskedFor(
  value: string | null | undefined,
): StoredSmsDraft["askedFor"] {
  if (
    value === "name" ||
    value === "phone" ||
    value === "service" ||
    value === "price"
  ) {
    return value;
  }
  return null;
}

function mapSmsThread(value: unknown): SmsThreadItem[] {
  const raw =
    typeof value === "string"
      ? (() => {
          try {
            return JSON.parse(value) as unknown;
          } catch {
            return [];
          }
        })()
      : value;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item) => item && typeof item === "object")
    .map((item) => {
      const row = item as Record<string, unknown>;
      return {
        role: row.role === "out" ? ("out" as const) : ("in" as const),
        text: String(row.text ?? ""),
        at: String(row.at ?? new Date().toISOString()),
      };
    });
}

function mapStoredSmsDraft(row: Record<string, unknown>): StoredSmsDraft {
  const amountRaw = row.amount_cents ?? row.amountCents;
  const amount = amountRaw == null ? null : Number(amountRaw);
  return {
    fromDigits: String(row.from_digits ?? row.fromDigits ?? ""),
    name: row.name ? String(row.name) : null,
    phone: row.phone ? String(row.phone) : null,
    email: row.email ? String(row.email) : null,
    service: row.service ? String(row.service) : null,
    amountCents: Number.isFinite(amount) ? amount : null,
    billing:
      row.billing === "monthly" || row.billing === "one_time"
        ? row.billing
        : null,
    address: row.address ? String(row.address) : null,
    askedFor: normalizeAskedFor(
      row.asked_for ? String(row.asked_for) : row.askedFor ? String(row.askedFor) : null,
    ),
    thread: mapSmsThread(row.thread),
    updatedAt: row.updated_at
      ? new Date(String(row.updated_at)).toISOString()
      : row.updatedAt
        ? String(row.updatedAt)
        : new Date().toISOString(),
  };
}

export function smsSenderKey(from: string) {
  const digits = phoneDigits(from);
  return digits.slice(-10) || digits || "unknown";
}

export function appendSmsThread(
  thread: SmsThreadItem[],
  role: SmsThreadItem["role"],
  text: string,
): SmsThreadItem[] {
  return [...thread, { role, text, at: new Date().toISOString() }].slice(
    -SMS_THREAD_MAX,
  );
}

function draftIsExpired(updatedAt: string) {
  const at = Date.parse(updatedAt);
  if (!Number.isFinite(at)) return true;
  return Date.now() - at > SMS_DRAFT_TTL_MS;
}

export async function getSmsDraft(from: string): Promise<StoredSmsDraft | null> {
  const key = smsSenderKey(from);
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT * FROM sms_drafts WHERE from_digits = $1 LIMIT 1`,
      [key],
    );
    const row = rows[0] as Record<string, unknown> | undefined;
    if (!row) return null;
    const stored = mapStoredSmsDraft(row);
    if (draftIsExpired(stored.updatedAt)) {
      await clearSmsDraft(from);
      return null;
    }
    return stored;
  }
  const store = await readJson();
  const stored = store.smsDrafts[key];
  if (!stored) return null;
  if (draftIsExpired(stored.updatedAt)) {
    delete store.smsDrafts[key];
    await writeJson(store);
    return null;
  }
  return stored;
}

export async function saveSmsDraft(
  from: string,
  input: Omit<StoredSmsDraft, "fromDigits" | "updatedAt">,
) {
  requireWriteStore();
  const record: StoredSmsDraft = {
    fromDigits: smsSenderKey(from),
    ...input,
    updatedAt: new Date().toISOString(),
  };
  const client = await sql();
  if (client) {
    await client.query(
      `INSERT INTO sms_drafts (
        from_digits, name, phone, email, service, amount_cents, billing,
        address, asked_for, thread, updated_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
      ON CONFLICT (from_digits) DO UPDATE SET
        name = EXCLUDED.name,
        phone = EXCLUDED.phone,
        email = EXCLUDED.email,
        service = EXCLUDED.service,
        amount_cents = EXCLUDED.amount_cents,
        billing = EXCLUDED.billing,
        address = EXCLUDED.address,
        asked_for = EXCLUDED.asked_for,
        thread = EXCLUDED.thread,
        updated_at = EXCLUDED.updated_at`,
      [
        record.fromDigits,
        record.name,
        record.phone,
        record.email,
        record.service,
        record.amountCents,
        record.billing,
        record.address,
        record.askedFor,
        JSON.stringify(record.thread),
        record.updatedAt,
      ],
    );
    return record;
  }
  const store = await readJson();
  store.smsDrafts[record.fromDigits] = record;
  await writeJson(store);
  return record;
}

export async function clearSmsDraft(from: string) {
  requireWriteStore();
  const key = smsSenderKey(from);
  const client = await sql();
  if (client) {
    await client.query(`DELETE FROM sms_drafts WHERE from_digits = $1`, [key]);
    return;
  }
  const store = await readJson();
  if (!store.smsDrafts[key]) return;
  delete store.smsDrafts[key];
  await writeJson(store);
}

export async function getAppSetting(key: string): Promise<string | null> {
  const client = await sql();
  if (client) {
    const { rows } = await client.query(
      `SELECT value FROM app_settings WHERE key = $1 LIMIT 1`,
      [key],
    );
    const value = (rows[0] as { value?: unknown } | undefined)?.value;
    return value ? String(value) : null;
  }
  if (process.env.VERCEL) return null;
  const store = await readJson();
  return store.settings[key] || null;
}

export async function setAppSetting(key: string, value: string) {
  const client = await sql();
  if (client) {
    await client.query(
      `INSERT INTO app_settings (key, value) VALUES ($1, $2)
       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`,
      [key, value],
    );
    return;
  }
  if (process.env.VERCEL) return;
  const store = await readJson();
  store.settings[key] = value;
  await writeJson(store);
}

export function quoteLabel(quote: Quote, customer?: Customer | null) {
  if (quote.serviceAddress) return quote.serviceAddress;
  if (quote.customerName) return quote.customerName;
  if (customer?.email) return customer.email;
  return "Quote";
}
