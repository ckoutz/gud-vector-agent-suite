import { redirect } from "next/navigation";

import { ServiceActions } from "@/components/portal/service-actions";
import { StatusChip } from "@/components/status-chip";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import {
  formatMoney,
  isCurrentCustomerQuote,
  quoteService,
  serviceStatusLabel,
} from "@/lib/quote-desk";
import { getCustomerById, listQuotesForEndUser } from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "My services | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function MyServicesPage() {
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
  const services = (await listQuotesForEndUser(customer)).filter(
    isCurrentCustomerQuote,
  );

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Customer
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        My services
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-neutral-600">
        Services you have set up. Pause makes a service inactive. Cancel marks
        it complete.
      </p>
      {services.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          No established services yet. Accept a quote and pay to add one here.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {services.map((quote) => (
            <li key={quote.id} className="card-peach rounded-[18px] p-5">
              <StatusChip>{serviceStatusLabel(quote.status)}</StatusChip>
              <p className="mt-1 text-xl font-semibold text-charcoal">
                {quoteService(quote)}
              </p>
              <p className="mt-2 text-sm text-neutral-700">
                {formatMoney(quote.amountCents, quote.currency)}
                {quote.billing === "monthly" ? " / month" : ""}
              </p>
              <ServiceActions quote={quote} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
