/* eslint-disable @next/next/no-img-element */
import { site } from "@/lib/site";

export function About() {
  return (
    <section id="about" className="section-white scroll-mt-24">
      <div className="mx-auto grid w-full max-w-[1100px] items-center gap-10 px-5 py-14 desktop:grid-cols-2 desktop:gap-16 desktop:px-8 desktop:py-20">
        <div>
          <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
            About
          </p>
          <h2 className="mt-3 max-w-[14ch] text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
            A San Francisco Bay Area shop.
          </h2>
          <p className="mt-8 max-w-[36rem] text-base leading-relaxed text-neutral-600">
            {site.about}
          </p>
        </div>
        <div className="flex justify-center desktop:justify-end">
          <img
            src="/logo.png"
            alt="Güd Vector"
            className="h-auto w-full max-w-[22rem]"
          />
        </div>
      </div>
    </section>
  );
}
