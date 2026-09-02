import { LegalDoc, LegalSection } from "@/components/legal-doc";
import { site } from "@/lib/site";

export const metadata = {
  title: "Terms | Güd Vector",
  description:
    "Terms for using the Güd Vector website and customer portal in the San Francisco Bay Area.",
};

export default function TermsPage() {
  return (
    <LegalDoc kicker="Terms" title="Using this site.">
      <LegalSection title="The shop">
        <p>
          {site.legalName} is a {site.areaServed} shop. The website and customer
          portal at {site.domain} are how we quote work, set up service, and take
          payment. Public contact is {site.email}.
        </p>
      </LegalSection>
      <LegalSection title="Quotes and payment">
        <p>
          A quote is an offer. Work starts after you accept. Payment is processed
          by Stripe. If your service can be paused or canceled, that happens in
          the portal.
        </p>
      </LegalSection>
      <LegalSection title="Accounts">
        <p>
          Email, Google, and phone sign-in create a customer account, not a shop
          login. Do not use the portal to open someone else’s quotes.
        </p>
      </LegalSection>
      <LegalSection title="Texts">
        <p>
          We use Twilio to send quote links and one-time login codes. Google
          sign-in uses Google’s account screen.
        </p>
      </LegalSection>
      <LegalSection title="Questions">
        <p>Email {site.email}.</p>
      </LegalSection>
    </LegalDoc>
  );
}
