"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusChip } from "@/components/status-chip";
import { formatMoney, liveQuoteLabel, quoteService } from "@/lib/quote-desk";
import type { Quote } from "@/lib/store";

export function CustomerQuoteCard({ quote }: { quote: Quote }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const status = liveQuoteLabel(quote.status) ?? quote.status;
  const waiting = quote.status === "sent" || quote.status === "draft";

  async function act(path: "accept" | "reject") {
    if (pending) return;
    setPending(true);
    setError("");
    const response = await fetch(`/api/quotes/${quote.id}/${path}`, {
      method: "POST",
    });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not update this quote.");
      return;
    }
    if (path === "accept") {
      router.push("/portal/payment");
      router.refresh();
      return;
    }
    router.refresh();
  }

  return (
    <li className="card-peach rounded-[18px] p-5">
      <StatusChip>{status}</StatusChip>
      <p className="mt-1 text-xl font-semibold text-charcoal">
        {quoteService(quote)}
      </p>
      <p className="mt-3 text-lg font-semibold text-charcoal">
        {formatMoney(quote.amountCents, quote.currency)}
        {quote.billing === "monthly" ? " / month" : ""}
      </p>
      {quote.status === "accepted" ? (
        <p className="mt-2 text-sm text-neutral-600">
          Accepted. Continue to payment when you are ready.
        </p>
      ) : null}
      {waiting ? (
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => void act("accept")}
            className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
          >
            {pending ? "Saving…" : "Accept"}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={() => void act("reject")}
            className="inline-flex h-11 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
          >
            Reject
          </button>
        </div>
      ) : quote.status === "accepted" ? (
        <a
          href="/portal/payment"
          className="mt-5 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504]"
        >
          Go to payment
        </a>
      ) : null}
      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </li>
  );
}
