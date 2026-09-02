import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { MarkInvoicePaidForm } from "@/components/portal/mark-invoice-paid-form";
import { StatusChip } from "@/components/status-chip";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import {
  invoiceStatusLabel,
  paidMethodCopy,
} from "@/lib/invoice-status";
import { formatMoney } from "@/lib/quote-desk";
import {
  getCustomerById,
  getInvoiceByStripeId,
  getQuoteById,
  getQuoteByStripeInvoiceId,
} from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Invoice | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function InvoiceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role !== "admin") redirect("/portal/payment");

  try {
    await bootstrapPortalUser();
  } catch {
    // ignore
  }

  const { id } = await params;
  const invoice = await getInvoiceByStripeId(decodeURIComponent(id));
  if (!invoice) notFound();

  const quote =
    (invoice.quoteId ? await getQuoteById(invoice.quoteId) : null) ||
    (await getQuoteByStripeInvoiceId(invoice.stripeInvoiceId));
  const customer = invoice.customerId
    ? await getCustomerById(invoice.customerId)
    : quote?.customerId
      ? await getCustomerById(quote.customerId)
      : null;
  const status = invoiceStatusLabel(invoice);
  const method = paidMethodCopy(invoice.paidMethod);
  const due = invoice.status !== "paid";

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Shop
      </p>
      <p className="mt-3">
        <Link
          href="/portal/invoices"
          className="text-sm font-semibold text-brand hover:underline"
        >
          All invoices
        </Link>
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Invoice
      </h1>

      <div className="card-peach mt-8 rounded-[18px] p-5">
        <StatusChip>{status}</StatusChip>
        <p className="mt-1 text-xl font-semibold text-charcoal">
          {invoice.description}
        </p>
        <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
          {quote?.customerName || customer?.name || customer?.email ? (
            <div>
              <dt className="inline font-semibold text-charcoal">Customer </dt>
              <dd className="inline">
                {quote?.customerName || customer?.name || customer?.email}
              </dd>
            </div>
          ) : null}
          {invoice.number ? (
            <div>
              <dt className="inline font-semibold text-charcoal">Number </dt>
              <dd className="inline">{invoice.number}</dd>
            </div>
          ) : null}
          <div>
            <dt className="inline font-semibold text-charcoal">Amount </dt>
            <dd className="inline">
              {formatMoney(invoice.amountCents, invoice.currency)}
            </dd>
          </div>
          {invoice.status === "paid" && method ? (
            <div>
              <dt className="inline font-semibold text-charcoal">Paid with </dt>
              <dd className="inline">{method}</dd>
            </div>
          ) : null}
          {invoice.paidAt ? (
            <div>
              <dt className="inline font-semibold text-charcoal">Paid on </dt>
              <dd className="inline">
                {new Date(invoice.paidAt).toLocaleDateString("en-US")}
              </dd>
            </div>
          ) : null}
          {invoice.paidNote ? (
            <div>
              <dt className="inline font-semibold text-charcoal">Note </dt>
              <dd className="inline">{invoice.paidNote}</dd>
            </div>
          ) : null}
        </dl>
        {due ? <MarkInvoicePaidForm invoiceId={invoice.stripeInvoiceId} /> : null}
      </div>
    </div>
  );
}
