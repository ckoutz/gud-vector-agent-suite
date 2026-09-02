"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { StatusChip } from "@/components/status-chip";
import {
  expiresInCopy,
  formatMoney,
  liveQuoteLabel,
  quoteService,
} from "@/lib/quote-desk";
import type { Quote } from "@/lib/store";

export function LiveQuoteCard({ quote }: { quote: Quote }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const status = liveQuoteLabel(quote.status) ?? quote.status;

  async function remove() {
    if (pending) return;
    const ok = window.confirm("Delete this quote? This cannot be undone.");
    if (!ok) return;
    setPending(true);
    setError("");
    const response = await fetch(`/api/quotes/${quote.id}`, { method: "DELETE" });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not delete this quote.");
      return;
    }
    router.refresh();
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const dollars = Number(data.get("price"));
    const response = await fetch(`/api/quotes/${quote.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customerName: data.get("customerName"),
        customerPhone: data.get("customerPhone"),
        customerEmail: data.get("customerEmail"),
        serviceAddress: data.get("serviceAddress"),
        service: data.get("service"),
        amountCents: Math.round(dollars * 100),
        billing: data.get("billing"),
      }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not save this quote.");
      return;
    }
    setEditing(false);
    router.refresh();
  }

  return (
    <li className="card-peach rounded-[18px] p-5">
      <div className="flex flex-col gap-4 desktop:flex-row desktop:items-start desktop:justify-between">
        <div className="min-w-0">
          <StatusChip>{status}</StatusChip>
          <p className="mt-1 text-xl font-semibold text-charcoal">
            {quote.customerName || "No name yet"}
          </p>
          <dl className="mt-3 grid gap-1 text-sm text-neutral-700">
            {quote.customerEmail ? (
              <div>
                <dt className="inline font-semibold text-charcoal">Email </dt>
                <dd className="inline">{quote.customerEmail}</dd>
              </div>
            ) : null}
            {quote.serviceAddress ? (
              <div>
                <dt className="inline font-semibold text-charcoal">Address </dt>
                <dd className="inline">{quote.serviceAddress}</dd>
              </div>
            ) : null}
            <div>
              <dt className="inline font-semibold text-charcoal">Service </dt>
              <dd className="inline">{quoteService(quote)}</dd>
            </div>
            <div>
              <dt className="inline font-semibold text-charcoal">Price </dt>
              <dd className="inline">
                {formatMoney(quote.amountCents, quote.currency)}
                {quote.billing === "monthly" ? " / month" : ""}
              </dd>
            </div>
            {quote.status === "sent" ? (
              <p className="pt-1 text-sm text-neutral-600">
                {expiresInCopy(quote.createdAt)}
              </p>
            ) : null}
          </dl>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={() => setEditing((open) => !open)}
            className="inline-flex h-11 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
          >
            {editing ? "Close" : "Manage"}
          </button>
        </div>
      </div>

      {editing ? (
        <form onSubmit={(event) => void save(event)} className="mt-5 border-t border-[#EDE4D8] pt-5">
          <div className="grid gap-3 desktop:grid-cols-2">
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Name</span>
              <input
                className="field"
                name="customerName"
                defaultValue={quote.customerName ?? ""}
                required
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Phone</span>
              <input
                className="field"
                name="customerPhone"
                defaultValue={quote.customerPhone ?? ""}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Email</span>
              <input
                className="field"
                name="customerEmail"
                type="email"
                defaultValue={quote.customerEmail ?? ""}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Address</span>
              <input
                className="field"
                name="serviceAddress"
                defaultValue={quote.serviceAddress ?? ""}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Service</span>
              <input
                className="field"
                name="service"
                defaultValue={quoteService(quote)}
                required
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">
                Price (USD)
              </span>
              <input
                className="field"
                name="price"
                type="number"
                min="0"
                step="0.01"
                defaultValue={(quote.amountCents / 100).toFixed(2)}
                required
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Billing</span>
              <select
                className="field"
                name="billing"
                defaultValue={quote.billing}
              >
                <option value="one_time">One-time</option>
                <option value="monthly">Monthly</option>
              </select>
            </label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={pending}
              className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
            >
              {pending ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => void remove()}
              className="inline-flex h-11 items-center justify-center rounded-full border border-red-200 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
            >
              Delete quote
            </button>
          </div>
        </form>
      ) : null}

      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </li>
  );
}
