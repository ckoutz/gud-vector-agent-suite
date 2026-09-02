import { redirect } from "next/navigation";
import Link from "next/link";

import { StatusChip } from "@/components/status-chip";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import { invoicePath, invoiceStatusLabel } from "@/lib/invoice-status";
import {
  formatMoney,
  isCurrentCustomerQuote,
  quoteService,
  serviceStatusLabel,
} from "@/lib/quote-desk";
import {
  ensureInvoiceForQuote,
  listQuotesForShop,
} from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Current customers | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function CurrentCustomersPage() {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role !== "admin") redirect("/portal");

  try {
    await bootstrapPortalUser();
  } catch {
    // ignore
  }

  const quotes = (await listQuotesForShop()).filter(isCurrentCustomerQuote);
  const rows = await Promise.all(
    quotes.map(async (quote) => {
      const invoice = await ensureInvoiceForQuote(quote);
      return { quote, invoice };
    }),
  );

  return (
    <div className="mx-auto w-full max-w-[1100px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Shop
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Current customers
      </h1>
      <p className="mt-2 max-w-[40rem] text-sm text-neutral-600">
        Customers with established service. Status is active, inactive, or
        complete.
      </p>

      {rows.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          No current customers yet. They appear here after they accept and pay.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {rows.map(({ quote, invoice }) => (
            <li key={quote.id} className="card-peach rounded-[18px] p-5">
              <StatusChip>{serviceStatusLabel(quote.status)}</StatusChip>
              <p className="mt-1 text-xl font-semibold text-charcoal">
                {quote.customerName || "No name yet"}
              </p>
              <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
                {quote.customerEmail ? (
                  <div>
                    <dt className="inline font-semibold text-charcoal">Email </dt>
                    <dd className="inline">{quote.customerEmail}</dd>
                  </div>
                ) : null}
                {quote.serviceAddress ? (
                  <div>
                    <dt className="inline font-semibold text-charcoal">Address </dt>
                    <dd className="inline">{quote.serviceAddress}</dd>
                  </div>
                ) : null}
                <div>
                  <dt className="inline font-semibold text-charcoal">Service </dt>
                  <dd className="inline">{quoteService(quote)}</dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-charcoal">Price </dt>
                  <dd className="inline">
                    {formatMoney(quote.amountCents, quote.currency)}
                    {quote.billing === "monthly" ? " / month" : ""}
                  </dd>
                </div>
                {invoice ? (
                  <div>
                    <dt className="inline font-semibold text-charcoal">
                      Invoice{" "}
                    </dt>
                    <dd className="inline">{invoiceStatusLabel(invoice)}</dd>
                  </div>
                ) : null}
              </dl>
              {invoice ? (
                <Link
                  href={invoicePath(invoice.stripeInvoiceId)}
                  className="mt-4 inline-block text-sm font-semibold text-brand hover:underline"
                >
                  Open invoice
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
