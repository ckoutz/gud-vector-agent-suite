import { redirect } from "next/navigation";

import { PortalHeader } from "@/components/portal/portal-header";
import { SignupForm } from "@/components/portal/signup-form";
import { StatusChip } from "@/components/status-chip";
import { getSession } from "@/lib/auth";
import { formatMoney, quoteService } from "@/lib/quote-desk";
import { getQuoteByToken } from "@/lib/store";
import {
  googleOAuthReady,
  providerSignInLead,
  twilioVerifyReady,
} from "@/lib/provider-flags";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Create your account | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ quote?: string; error?: string }>;
}) {
  const session = await getSession();
  if (session) redirect("/portal");

  const params = await searchParams;
  const quoteToken = params.quote?.trim() ?? "";
  const quote = quoteToken ? await getQuoteByToken(quoteToken) : null;
  const googleReady = googleOAuthReady();
  const phoneReady = twilioVerifyReady();

  return (
    <>
      <PortalHeader />
      <div className="mx-auto w-full max-w-md px-5 py-12">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          Customer
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
          Create your account.
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-neutral-600">
          {providerSignInLead(googleReady, phoneReady)} After you sign up you
          can accept or reject your quote, then pay on this site.
        </p>
        {quote ? (
          <div className="card-peach mt-6 rounded-[18px] p-5">
            <StatusChip>Quote</StatusChip>
            <p className="mt-1 text-xl font-semibold text-charcoal">
              {quoteService(quote)}
            </p>
            <p className="mt-2 text-sm text-neutral-700">
              {formatMoney(quote.amountCents, quote.currency)}
              {quote.billing === "monthly" ? " / month" : ""}
            </p>
          </div>
        ) : null}
        <div className="mt-8">
          <SignupForm
            quoteToken={quoteToken}
            googleReady={googleReady}
            phoneReady={phoneReady}
            errorFromServer={params.error}
          />
        </div>
      </div>
    </>
  );
}
