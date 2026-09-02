import { CtaLink } from "@/components/cta-link";
import { PhonePreview } from "@/components/phone-preview";
import { site } from "@/lib/site";

export function Hero() {
  return (
    <section id="top" className="section-white">
      <div className="mx-auto grid w-full max-w-[1100px] items-center gap-12 px-5 py-12 desktop:grid-cols-2 desktop:gap-16 desktop:px-8 desktop:py-20">
        <div>
          <p className="text-bubble inline-flex rounded-full px-3 py-1 text-[0.7rem] font-semibold tracking-[0.14em] uppercase">
            {site.areaServed}
          </p>
          <p className="mt-5 max-w-[36rem] text-base font-semibold text-brand">
            {site.motto}
          </p>
          <h1 className="mt-4 max-w-[16ch] text-[2.2rem] font-semibold tracking-[-0.03em] text-charcoal sm:text-5xl desktop:text-[3.15rem] desktop:leading-[1.08]">
            {site.headline}
          </h1>
          <p className="mt-5 max-w-[38rem] text-lg leading-relaxed text-neutral-700">
            {site.lede}
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
            <CtaLink href="/#contact">Get in touch</CtaLink>
            <CtaLink href="/#how-it-works" variant="outline">
              See how it works
            </CtaLink>
          </div>
        </div>
        <PhonePreview />
      </div>
    </section>
  );
}
