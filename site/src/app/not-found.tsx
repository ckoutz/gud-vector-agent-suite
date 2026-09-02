/* eslint-disable @next/next/no-img-element */
import { CtaLink } from "@/components/cta-link";
import { site } from "@/lib/site";

export default function NotFound() {
  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col items-start gap-6 px-5 py-20 desktop:px-8">
      <img src="/header-logo.png" alt="Güd Vector" className="h-20 w-auto" />
      <h1 className="max-w-[16ch] text-4xl font-semibold tracking-tight text-charcoal">
        That page is not on this site.
      </h1>
      <p className="max-w-[36rem] text-base leading-relaxed text-neutral-600">
        Head back home, or use Get in touch on the main page. Public email is{" "}
        {site.email}.
      </p>
      <div className="flex flex-col gap-3 sm:flex-row">
        <CtaLink href="/">Back home</CtaLink>
        <CtaLink href="/#contact" variant="outline">
          Get in touch
        </CtaLink>
      </div>
    </div>
  );
}
