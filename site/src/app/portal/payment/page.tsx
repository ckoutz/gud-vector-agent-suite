import { redirect } from "next/navigation";

import { PayOnSiteButton } from "@/components/portal/pay-gud-vector-button";
import { StatusChip } from "@/components/status-chip";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import { invoiceStatusLabel } from "@/lib/invoice-status";
import { formatMoney, quoteService } from "@/lib/quote-desk";
import { invoicePaymentsReady, refreshClientInvoices } from "@/lib/stripe";
import {
  ensureInvoiceForQuote,
  getCustomerById,
  listQuotesForEndUser,
} from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Payment | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function PaymentPage({
  searchParams,
}: {
  searchParams: Promise<{
    payment_intent?: string;
    redirect_status?: string;
  }>;
}) {
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
  const dueQuotes = (await listQuotesForEndUser(customer)).filter(
    (quote) => quote.status === "accepted",
  );
  const due = (
    await Promise.all(
      dueQuotes.map(async (quote) => {
        const invoice = await ensureInvoiceForQuote(quote);
        return { quote, invoice };
      }),
    )
  ).filter(({ invoice }) => invoice?.status !== "paid");

  const params = await searchParams;
  const returningFromAuth =
    params.redirect_status === "succeeded" || Boolean(params.payment_intent);

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Customer
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Payment
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-600">
        Pay the current amount here. Card details stay on this page.
      </p>
      {returningFromAuth ? (
        <p className="mt-4 text-sm text-neutral-600">
          If you just paid, this list updates when the payment is confirmed.
        </p>
      ) : null}

      {due.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          Nothing is due right now. Accept a quote first, or check past
          payments.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {due.map(({ quote, invoice }) => (
            <li key={quote.id} className="card-peach rounded-[18px] p-5">
              <StatusChip>
                {invoice ? invoiceStatusLabel(invoice) : "Due"}
              </StatusChip>
              <p className="mt-1 text-xl font-semibold text-charcoal">
                {quoteService(quote)}
              </p>
              <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
                {invoice?.number ? (
                  <div>
                    <dt className="inline font-semibold text-charcoal">
                      Invoice{" "}
                    </dt>
                    <dd className="inline">{invoice.number}</dd>
                  </div>
                ) : null}
                <div>
                  <dt className="inline font-semibold text-charcoal">Amount </dt>
                  <dd className="inline">
                    {formatMoney(quote.amountCents, quote.currency)}
                    {quote.billing === "monthly" ? " / month" : ""}
                  </dd>
                </div>
              </dl>
              <div className="mt-5">
                <PayOnSiteButton
                  quoteId={quote.id}
                  invoiceId={quote.stripeInvoiceId}
                  paymentsReady={invoicePaymentsReady()}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
