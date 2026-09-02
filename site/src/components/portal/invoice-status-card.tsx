import Link from "next/link";

import { StatusChip } from "@/components/status-chip";
import { formatMoney } from "@/lib/quote-desk";
import { invoicePath, invoiceStatusLabel } from "@/lib/invoice-status";
import type { ClientInvoice } from "@/lib/store";

export function InvoiceStatusCard({
  invoice,
  customerName,
  href,
}: {
  invoice: ClientInvoice;
  customerName?: string | null;
  href?: string | null;
}) {
  const status = invoiceStatusLabel(invoice);
  const body = (
    <>
      <StatusChip>{status}</StatusChip>
      <p className="mt-1 text-xl font-semibold text-charcoal">
        {invoice.description}
      </p>
      <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
        {customerName ? (
          <div>
            <dt className="inline font-semibold text-charcoal">Customer </dt>
            <dd className="inline">{customerName}</dd>
          </div>
        ) : null}
        {invoice.number ? (
          <div>
            <dt className="inline font-semibold text-charcoal">Invoice </dt>
            <dd className="inline">{invoice.number}</dd>
          </div>
        ) : null}
        <div>
          <dt className="inline font-semibold text-charcoal">Amount </dt>
          <dd className="inline">
            {formatMoney(invoice.amountCents, invoice.currency)}
          </dd>
        </div>
      </dl>
    </>
  );

  const to = href ?? invoicePath(invoice.stripeInvoiceId);
  if (!to) {
    return <li className="card-peach rounded-[18px] p-5">{body}</li>;
  }

  return (
    <li>
      <Link
        href={to}
        className="card-peach block rounded-[18px] p-5 hover:border-brand"
      >
        {body}
        <p className="mt-4 text-sm font-semibold text-brand">Open invoice</p>
      </Link>
    </li>
  );
}
