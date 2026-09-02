import { redirect } from "next/navigation";

import { StatusChip } from "@/components/status-chip";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import { invoiceStatusLabel } from "@/lib/invoice-status";
import { formatMoney } from "@/lib/quote-desk";
import {
  ensureInvoiceForQuote,
  getCustomerById,
  isPortalInvoice,
  listInvoicesForCustomer,
  listQuotesForEndUser,
} from "@/lib/store";
import { refreshClientInvoices } from "@/lib/stripe";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Past payments | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function PastPaymentsPage() {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role === "admin") redirect("/portal");

  try {
    await bootstrapPortalUser();
  } catch {
    // ignore
  }

  const customer = await getCustomerById(session.customerId);
  if (!customer) redirect("/portal/login");
  await refreshClientInvoices(customer);
  const quotes = await listQuotesForEndUser(customer);
  for (const quote of quotes) {
    if (
      quote.stripeInvoiceId ||
      quote.status === "paid" ||
      quote.status === "accepted" ||
      quote.status === "paused" ||
      quote.status === "acknowledged"
    ) {
      await ensureInvoiceForQuote(quote);
    }
  }

  const paidInvoices = (await listInvoicesForCustomer(customer)).filter(
    (invoice) => isPortalInvoice(invoice) && invoice.status === "paid",
  );

  const rows = paidInvoices
    .map((invoice) => ({
      key: invoice.stripeInvoiceId,
      title: invoice.description,
      number: invoice.number,
      amountCents: invoice.amountCents,
      currency: invoice.currency,
      when: invoice.paidAt ?? invoice.createdAt,
      status: invoiceStatusLabel(invoice),
    }))
    .sort((a, b) => b.when.localeCompare(a.when));

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Customer
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Past payments
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-600">
        Payments that have already gone through.
      </p>
      {rows.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          No past payments yet.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {rows.map((row) => (
            <li key={row.key} className="card-peach rounded-[18px] p-5">
              <StatusChip>{row.status}</StatusChip>
              <p className="mt-1 text-xl font-semibold text-charcoal">
                {row.title}
              </p>
              <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
                {row.number ? (
                  <div>
                    <dt className="inline font-semibold text-charcoal">
                      Invoice{" "}
                    </dt>
                    <dd className="inline">{row.number}</dd>
                  </div>
                ) : null}
                <div>
                  <dt className="inline font-semibold text-charcoal">Amount </dt>
                  <dd className="inline">
                    {formatMoney(row.amountCents, row.currency)}
                  </dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-charcoal">Date </dt>
                  <dd className="inline">
                    {new Date(row.when).toLocaleDateString("en-US")}
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
