/* eslint-disable @next/next/no-img-element */

export function PhonePreview() {
  return (
    <div className="mx-auto w-full max-w-[22rem] desktop:mx-0 desktop:justify-self-end">
      <p className="mb-3 text-center text-[0.68rem] font-semibold tracking-[0.16em] text-neutral-500 uppercase">
        What a customer sees on their phone
      </p>

      <div className="relative mx-auto w-[322px]">
        <span
          aria-hidden="true"
          className="absolute top-[108px] -left-[3px] z-10 h-[18px] w-[3px] rounded-l-sm bg-[#6d6d70]"
        />
        <span
          aria-hidden="true"
          className="absolute top-[142px] -left-[3px] z-10 h-[34px] w-[3px] rounded-l-sm bg-[#6d6d70]"
        />
        <span
          aria-hidden="true"
          className="absolute top-[184px] -left-[3px] z-10 h-[34px] w-[3px] rounded-l-sm bg-[#6d6d70]"
        />
        <span
          aria-hidden="true"
          className="absolute top-[156px] -right-[3px] z-10 h-[52px] w-[3px] rounded-r-sm bg-[#6d6d70]"
        />

        <div className="relative rounded-[42px] bg-linear-to-b from-[#5c5c60] via-[#3a3a3e] to-[#1f1f22] p-[3px] shadow-[0_28px_56px_-22px_rgba(28,28,28,0.55)]">
          <div className="rounded-[39px] bg-[#2a2a2d] p-[8px]">
            <div
              className="phone-screen relative min-h-[680px] w-[300px] overflow-hidden rounded-[31px] bg-white"
              style={{ backgroundColor: "#ffffff" }}
            >
              <span
                aria-hidden="true"
                className="pointer-events-none absolute top-3 left-1/2 z-20 h-[22px] w-[96px] -translate-x-1/2 rounded-full bg-black"
              />

              <div className="relative flex items-center justify-between px-6 pt-3.5 text-[11px] font-semibold text-charcoal">
                <span>9:41</span>
                <span className="flex items-center gap-1" aria-hidden="true">
                  <svg viewBox="0 0 18 12" className="h-2.5 w-[18px]" fill="currentColor">
                    <rect x="0" y="7" width="3" height="5" rx="0.6" />
                    <rect x="5" y="5" width="3" height="7" rx="0.6" />
                    <rect x="10" y="2.5" width="3" height="9.5" rx="0.6" />
                    <rect x="15" y="0" width="3" height="12" rx="0.6" opacity="0.35" />
                  </svg>
                  <svg viewBox="0 0 16 12" className="h-2.5 w-4" fill="currentColor">
                    <path d="M8 3.2c1.8 0 3.4.7 4.6 1.8l1.2-1.3A8.3 8.3 0 0 0 8 1.4 8.3 8.3 0 0 0 2.2 3.7L3.4 5A6.5 6.5 0 0 1 8 3.2Zm0 3.2c.9 0 1.8.4 2.4 1l1.2-1.3A5.3 5.3 0 0 0 8 4.6 5.3 5.3 0 0 0 4.4 6.1L5.6 7.4A3.6 3.6 0 0 1 8 6.4Zm0 3.1a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z" />
                  </svg>
                  <svg viewBox="0 0 25 12" className="h-2.5 w-[25px]">
                    <rect
                      x="0.6"
                      y="1.2"
                      width="20"
                      height="9.6"
                      rx="2.2"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.2"
                    />
                    <rect x="2" y="2.6" width="15.4" height="6.8" rx="1.2" fill="currentColor" />
                    <rect x="21.4" y="4" width="1.6" height="4" rx="0.6" fill="currentColor" />
                  </svg>
                </span>
              </div>

              <div className="flex min-h-[620px] flex-col px-5 pt-8 pb-3">
                <img
                  src="/your-business-logo.png?v=2"
                  alt="Your business"
                  className="h-12 w-auto max-w-full border-0 bg-transparent object-contain object-left shadow-none"
                />
                <a
                  href="/#contact"
                  className="mt-8 rounded-full bg-brand px-4 py-3 text-center text-sm font-semibold text-white hover:bg-[#e56504]"
                >
                  Book now
                </a>
                <ul className="mt-5 space-y-2">
                  {[
                    "Your actual services, listed clearly",
                    "Built for a phone, not a desktop leftover",
                    "A way to reach you without hunting",
                  ].map((item) => (
                    <li
                      key={item}
                      className="text-bubble rounded-xl px-3 py-2.5 text-sm"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
                <div className="mt-auto flex justify-center pt-6 pb-1">
                  <span
                    aria-hidden="true"
                    className="h-[5px] w-[108px] rounded-full bg-black/85"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
