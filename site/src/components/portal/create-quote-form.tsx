"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

export function CreateQuoteForm() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const form = event.currentTarget;
    const data = new FormData(form);
    const dollars = Number(data.get("price"));
    const response = await fetch("/api/quotes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customerName: data.get("customerName"),
        customerPhone: data.get("customerPhone"),
        customerEmail: data.get("customerEmail"),
        serviceAddress: data.get("serviceAddress"),
        service: data.get("service"),
        amountCents: Math.round(dollars * 100),
        billing: data.get("billing") === "monthly" ? "monthly" : "one_time",
      }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not create that quote.");
      return;
    }
    form.reset();
    setOpen(false);
    router.refresh();
  }

  return (
    <div className="mt-6">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504]"
      >
        {open ? "Close" : "Create a new quote"}
      </button>
      {open ? (
        <form
          onSubmit={(event) => void onSubmit(event)}
          className="card-peach mt-4 rounded-[18px] p-5"
        >
          <div className="grid gap-3 desktop:grid-cols-2">
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Customer</span>
              <input className="field" name="customerName" required />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Phone</span>
              <input className="field" name="customerPhone" />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Email</span>
              <input className="field" name="customerEmail" type="email" />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Address</span>
              <input className="field" name="serviceAddress" />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Service</span>
              <input className="field" name="service" required />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Price (USD)</span>
              <input
                className="field"
                name="price"
                type="number"
                min="0"
                step="0.01"
                required
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-sm font-semibold text-charcoal">Billing</span>
              <select className="field" name="billing" defaultValue="one_time">
                <option value="one_time">One-time</option>
                <option value="monthly">Monthly</option>
              </select>
            </label>
          </div>
          {error ? (
            <p className="mt-3 text-sm text-red-700" role="alert">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={pending}
            className="mt-4 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
          >
            {pending ? "Saving…" : "Save quote"}
          </button>
        </form>
      ) : null}
    </div>
  );
}
