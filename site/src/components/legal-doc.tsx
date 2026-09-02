import type { ReactNode } from "react";

import { CtaLink } from "@/components/cta-link";
import { site } from "@/lib/site";

export function LegalDoc({
  kicker,
  title,
  children,
}: {
  kicker: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <article className="mx-auto w-full max-w-[720px] px-5 py-14 desktop:px-8 desktop:py-20">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        {kicker}
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal desktop:text-4xl">
        {title}
      </h1>
      <p className="mt-3 text-sm text-neutral-600">
        {site.legalName} · {site.areaServed} · {site.email}
      </p>
      <div className="mt-10 grid gap-8 text-base leading-relaxed text-neutral-700">
        {children}
      </div>
      <p className="mt-10 text-sm text-neutral-500">Updated August 2026.</p>
      <div className="mt-8">
        <CtaLink href="/#contact">Get in touch</CtaLink>
      </div>
    </article>
  );
}

export function LegalSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section>
      <h2 className="text-lg font-semibold text-charcoal">{title}</h2>
      <div className="mt-2 grid gap-3">{children}</div>
    </section>
  );
}
