/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

import { site } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer className="section-white border-t border-[#EDE4D8]">
      <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-5 px-5 py-8 desktop:flex-row desktop:items-center desktop:justify-between desktop:px-8">
        <Link href="/#top" className="block shrink-0">
          <img
            src="/header-logo.png"
            alt="Güd Vector"
            className="h-[72px] w-auto max-w-[180px] object-contain desktop:h-[112px] desktop:max-w-[280px]"
          />
        </Link>
        <p className="text-sm leading-relaxed text-neutral-600">
          {site.legalName}
          <span className="mx-2 text-neutral-300" aria-hidden="true">
            ·
          </span>
          {site.domain}
          <span className="mx-2 text-neutral-300" aria-hidden="true">
            ·
          </span>
          <span className="select-all font-medium text-charcoal">{site.email}</span>
        </p>
        <p className="text-sm text-neutral-600">
          <Link href="/privacy" className="font-medium text-charcoal hover:text-brand">
            Privacy
          </Link>
          <span className="mx-2 text-neutral-300" aria-hidden="true">
            ·
          </span>
          <Link href="/terms" className="font-medium text-charcoal hover:text-brand">
            Terms
          </Link>
        </p>
      </div>
    </footer>
  );
}
