"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoutButton } from "@/components/portal/logout-button";
import type { PortalRole } from "@/lib/store";

export function PortalNav({ role }: { role: PortalRole }) {
  const pathname = usePathname();
  const shop = role === "admin";
  const links = shop
    ? [
        { href: "/portal", label: "Live quotes" },
        { href: "/portal/customers", label: "Current customers" },
        { href: "/portal/invoices", label: "Invoices" },
      ]
    : [
        { href: "/portal", label: "Quotes" },
        { href: "/portal/services", label: "My services" },
        { href: "/portal/payment", label: "Payment" },
        { href: "/portal/payments", label: "Past payments" },
        { href: "/portal/profile", label: "Profile" },
      ];

  return (
    <aside className="section-white w-full shrink-0 border-b border-[#EDE4D8] desktop:w-60 desktop:border-r desktop:border-b-0">
      <div className="flex flex-col gap-6 px-5 py-4 desktop:min-h-full desktop:px-5 desktop:py-6">
        <Link
          href="/"
          className="block shrink-0"
          aria-label="Güd Vector marketing homepage"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/header-logo.png"
            alt="Güd Vector"
            className="h-[56px] w-auto max-w-[160px] object-contain desktop:h-[72px] desktop:max-w-[200px]"
          />
        </Link>
        <nav aria-label="Portal" className="flex flex-col gap-1">
          {links.map((link) => {
            const active =
              link.href === "/portal"
                ? pathname === "/portal" || pathname.startsWith("/portal/quotes")
                : pathname === link.href || pathname.startsWith(`${link.href}/`);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-lg px-3 py-2 text-sm font-medium ${
                  active
                    ? "text-bubble"
                    : "border border-transparent text-charcoal hover:text-brand"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto flex flex-col gap-2 pt-4">
          <p className="px-3 text-[0.7rem] font-semibold tracking-[0.16em] text-neutral-500 uppercase">
            {shop ? "Shop" : "Customer"}
          </p>
          <div className="px-3 py-2">
            <LogoutButton />
          </div>
        </div>
      </div>
    </aside>
  );
}
