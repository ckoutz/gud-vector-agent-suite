import { redirect } from "next/navigation";

import { LoginForm } from "@/components/portal/login-form";
import { PortalHeader } from "@/components/portal/portal-header";
import { getSession } from "@/lib/auth";
import {
  googleOAuthReady,
  providerSignInLead,
  twilioVerifyReady,
} from "@/lib/provider-flags";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Customer portal login | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function PortalLoginPage({
  searchParams,
}: {
  searchParams: Promise<{ quote?: string; error?: string }>;
}) {
  const session = await getSession();
  if (session) redirect("/portal");
  const params = await searchParams;
  const googleReady = googleOAuthReady();
  const phoneReady = twilioVerifyReady();

  return (
    <>
      <PortalHeader />
      <div className="mx-auto w-full max-w-md px-5 py-12">
        <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
          Customer portal
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
          Log in to your portal.
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-neutral-600">
          {providerSignInLead(googleReady, phoneReady)} Public contact for new
          work is info@gudvector.com.
        </p>
        <div className="mt-8">
          <LoginForm
            quoteToken={params.quote?.trim() ?? ""}
            googleReady={googleReady}
            phoneReady={phoneReady}
            errorFromServer={params.error}
          />
        </div>
      </div>
    </>
  );
}
