/* eslint-disable @next/next/no-img-element */
"use client";

import { useState } from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { CtaLink } from "@/components/cta-link";
import { nav } from "@/lib/site";

export function SiteHeader() {
  const [open, setOpen] = useState(false);

  return (
    <header className="section-white sticky top-0 z-50 border-b border-[#EDE4D8]">
      <div className="mx-auto flex w-full max-w-[1100px] items-center justify-between gap-3 px-4 py-2 desktop:px-8 desktop:py-3">
        <Link
          href="/#top"
          className="block shrink-0"
          onClick={() => setOpen(false)}
        >
          <img
            src="/header-logo.png"
            alt="Güd Vector"
            className="h-[72px] w-auto max-w-[180px] object-contain desktop:h-[112px] desktop:max-w-[280px]"
          />
        </Link>

        <nav
          aria-label="Primary"
          className="hidden items-center gap-7 desktop:flex"
        >
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="text-sm font-medium text-charcoal"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <CtaLink
            href="/#contact"
            className="hidden h-10 px-4 desktop:inline-flex"
          >
            Get in touch
          </CtaLink>
          <CtaLink href="/#contact" className="h-9 px-3 text-sm desktop:hidden">
            Get in touch
          </CtaLink>
          <button
            type="button"
            className="inline-flex size-10 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] text-charcoal desktop:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? (
              <X className="size-5" aria-hidden="true" />
            ) : (
              <Menu className="size-5" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>

      {open ? (
        <div
          id="mobile-nav"
          className="border-t border-neutral-200 bg-[#FDFDFD] desktop:hidden"
        >
          <nav
            aria-label="Mobile"
            className="mx-auto flex max-w-[1100px] flex-col gap-1 px-5 py-3"
          >
            {nav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-lg px-2 py-2.5 text-base font-medium text-charcoal"
                onClick={() => setOpen(false)}
              >
                {item.label}
              </Link>
            ))}
            <Link
              href="/#contact"
              className="rounded-lg px-2 py-2.5 text-base font-medium text-brand"
              onClick={() => setOpen(false)}
            >
              Get in touch
            </Link>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
