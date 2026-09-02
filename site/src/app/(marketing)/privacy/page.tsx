import { LegalDoc, LegalSection } from "@/components/legal-doc";
import { site } from "@/lib/site";

export const metadata = {
  title: "Privacy | Güd Vector",
  description:
    "How Güd Vector Consulting Services uses name, email, and phone for quotes, accounts, Google login, Stripe payments, and Twilio texts.",
};

export default function PrivacyPage() {
  return (
    <LegalDoc kicker="Privacy" title="How we use your information.">
      <LegalSection title="Who we are">
        <p>
          {site.legalName} is a {site.areaServed} shop. This page is for the
          website and customer portal at {site.domain}. Questions go to{" "}
          {site.email}.
        </p>
      </LegalSection>
      <LegalSection title="What we collect">
        <p>
          We collect the name, email, and phone number you give us when you
          contact us, open a quote, or create a portal account.
        </p>
      </LegalSection>
      <LegalSection title="How we use it">
        <p>We use that information to:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Send quotes and run your customer portal account</li>
          <li>Let you sign in with Google (Google shares your name and email)</li>
          <li>Take payment through Stripe</li>
          <li>
            Send quote links and one-time login codes by SMS through Twilio
            Verify
          </li>
        </ul>
        <p>We do not sell this information.</p>
      </LegalSection>
      <LegalSection title="How long we keep it">
        <p>
          We keep quote, account, and payment records while we work with you,
          and as needed for bookkeeping.
        </p>
      </LegalSection>
      <LegalSection title="Contact">
        <p>
          Email {site.email} if you want us to update or remove your account
          details.
        </p>
      </LegalSection>
    </LegalDoc>
  );
}
