/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

import { LogoutButton } from "@/components/portal/logout-button";

export function PortalHeader({ signedIn }: { signedIn?: boolean }) {
  return (
    <header className="section-white border-b border-[#EDE4D8]">
      <div className="mx-auto flex w-full max-w-[1100px] items-center justify-between gap-3 px-5 py-3 desktop:px-8">
        <Link
          href="/"
          className="block shrink-0"
          aria-label="Güd Vector marketing homepage"
        >
          <img
            src="/header-logo.png"
            alt="Güd Vector"
            className="h-[64px] w-auto max-w-[180px] object-contain desktop:h-[88px] desktop:max-w-[240px]"
          />
        </Link>
        {signedIn ? (
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2">
            <LogoutButton />
          </div>
        ) : null}
      </div>
    </header>
  );
}
