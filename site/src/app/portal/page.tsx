import { redirect } from "next/navigation";

import { CreateQuoteForm } from "@/components/portal/create-quote-form";
import { CustomerQuoteCard } from "@/components/portal/customer-quote-card";
import { LiveQuoteCard } from "@/components/portal/live-quote-card";
import { bootstrapPortalUser, getSession } from "@/lib/auth";
import { isEndUserOpenQuote, isLiveQuote } from "@/lib/quote-desk";
import {
  getCustomerById,
  listQuotesForEndUser,
  listQuotesForShop,
} from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Quotes | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function PortalHomePage() {
  const session = await getSession();
  if (!session) redirect("/portal/login");

  try {
    await bootstrapPortalUser();
  } catch {
    // ignore
  }

  if (session.role === "admin") {
    const quotes = (await listQuotesForShop()).filter(isLiveQuote);
    return (
      <div className="mx-auto w-full max-w-[1100px] px-5 py-10 desktop:px-8">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          Shop
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
          Live quotes
        </h1>
        <p className="mt-2 max-w-[40rem] text-sm text-neutral-600">
          Quotes waiting on a customer. Sent quotes drop off after 14 days if
          they are not accepted. Rejected quotes leave this queue.
        </p>
        <CreateQuoteForm />
        {quotes.length === 0 ? (
          <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
            No live quotes.
          </p>
        ) : (
          <ul className="mt-8 grid gap-4">
            {quotes.map((quote) => (
              <LiveQuoteCard key={quote.id} quote={quote} />
            ))}
          </ul>
        )}
      </div>
    );
  }

  const customer = await getCustomerById(session.customerId);
  if (!customer) redirect("/portal/login");
  const quotes = (await listQuotesForEndUser(customer)).filter(
    isEndUserOpenQuote,
  );

  return (
    <div className="mx-auto w-full max-w-[1100px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Customer
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Quotes
      </h1>
      <p className="mt-2 max-w-[40rem] text-sm text-neutral-600">
        Quotes sent to you. Accept to continue to payment, or reject to decline.
      </p>
      {quotes.length === 0 ? (
        <p className="card-peach mt-8 rounded-[18px] p-6 text-neutral-600">
          No quotes waiting for you.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4">
          {quotes.map((quote) => (
            <CustomerQuoteCard key={quote.id} quote={quote} />
          ))}
        </ul>
      )}
    </div>
  );
}
