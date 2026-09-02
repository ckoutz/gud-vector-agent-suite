/* eslint-disable @next/next/no-img-element */
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { formatMoney } from "@/lib/quote-desk";
import { getSession } from "@/lib/auth";
import { getQuoteByToken } from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Quote | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function PublicQuotePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const session = await getSession();
  if (session) redirect("/portal");

  const { token } = await params;
  const quote = await getQuoteByToken(token);
  if (!quote) notFound();

  const service = quote.items[0]?.description ?? "Service";
  const priceLabel =
    quote.billing === "monthly"
      ? `${formatMoney(quote.amountCents, quote.currency)} / month`
      : formatMoney(quote.amountCents, quote.currency);
  const signupHref = `/portal/signup?quote=${encodeURIComponent(quote.claimToken)}`;
  const loginHref = `/portal/login?quote=${encodeURIComponent(quote.claimToken)}`;

  return (
    <div className="flex min-h-full flex-col bg-[#FDFDFD]">
      <header className="section-white border-b border-[#EDE4D8]">
        <div className="mx-auto flex w-full max-w-[640px] items-center justify-between gap-3 px-5 py-3">
          <Link href="/" className="block shrink-0" aria-label="Güd Vector marketing homepage">
            <img
              src="/header-logo.png"
              alt="Güd Vector"
              className="h-[64px] w-auto max-w-[180px] object-contain"
            />
          </Link>
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
            <Link
              href={loginHref}
              className="text-sm font-medium text-charcoal hover:text-brand"
            >
              Log in
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[640px] px-5 py-10">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          Quote
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
          {service}
        </h1>
        {quote.customerName ? (
          <p className="mt-2 text-base text-neutral-700">{quote.customerName}</p>
        ) : null}
        {quote.serviceAddress ? (
          <p className="mt-2 text-base text-neutral-700">{quote.serviceAddress}</p>
        ) : null}
        <p className="mt-4 text-2xl font-semibold text-charcoal">{priceLabel}</p>

        <ul className="card-peach mt-8 rounded-[18px] p-5">
          {quote.items.map((item) => (
            <li
              key={item.id}
              className="flex items-start justify-between gap-4 py-2"
            >
              <span className="text-charcoal">{item.description}</span>
              <span className="font-semibold text-charcoal">
                {formatMoney(item.amountCents * item.quantity, quote.currency)}
              </span>
            </li>
          ))}
        </ul>

        {quote.status === "sent" || quote.status === "draft" ? (
          <div className="mt-8 flex flex-col gap-3">
            <Link
              href={signupHref}
              className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504]"
            >
              Create an account to accept or reject
            </Link>
            <p className="text-sm text-neutral-600">
              Already have an account?{" "}
              <Link href={loginHref} className="font-semibold text-brand">
                Log in
              </Link>
            </p>
          </div>
        ) : quote.status === "accepted" ? (
          <p className="mt-8 text-sm text-neutral-600">
            Accepted.{" "}
            <Link href={loginHref} className="font-semibold text-brand">
              Log in
            </Link>{" "}
            to pay on the portal.
          </p>
        ) : quote.status === "rejected" ? (
          <p className="mt-8 text-sm text-neutral-600">This quote was rejected.</p>
        ) : null}
      </main>
    </div>
  );
}
