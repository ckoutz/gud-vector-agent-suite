import { redirect } from "next/navigation";

import { InvoiceStatusCard } from "@/components/portal/invoice-status-card";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import {
  getCustomerById,
  getQuoteById,
  getQuoteByStripeInvoiceId,
  listInvoicesForShop,
} from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Invoices | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function InvoicesPage() {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role !== "admin") redirect("/portal/payment");

  try {
    await bootstrapPortalUser();
  } catch {
    // ignore
  }

  const invoices = await listInvoicesForShop();
  const rows = await Promise.all(
    invoices.map(async (invoice) => {
      const quote =
        (invoice.quoteId ? await getQuoteById(invoice.quoteId) : null) ||
        (await getQuoteByStripeInvoiceId(invoice.stripeInvoiceId));
      const customer = invoice.customerId
        ? await getCustomerById(invoice.customerId)
        : quote?.customerId
          ? await getCustomerById(quote.customerId)
          : null;
      return {
        invoice,
        customerName:
          quote?.customerName || customer?.name || customer?.email || null,
      };
    }),
  );

  return (
    <div className="mx-auto w-full max-w-[1100px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Shop
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Invoices
      </h1>
      <p className="mt-2 max-w-[40rem] text-sm text-neutral-600">
        Due, paid by card, or paid by cash or check. Open an invoice to record
        money collected in person.
      </p>

      {rows.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          No invoices yet. They appear here from accepted quotes and from Stripe
          card payments.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {rows.map(({ invoice, customerName }) => (
            <InvoiceStatusCard
              key={invoice.stripeInvoiceId}
              invoice={invoice}
              customerName={customerName}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
