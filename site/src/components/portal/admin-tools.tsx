"use client";

import { useState, type FormEvent } from "react";

import { StatusChip } from "@/components/status-chip";
import type { Customer, Quote } from "@/lib/store";

export function AdminTools({
  customers,
  quotes,
}: {
  customers: Customer[];
  quotes: Quote[];
}) {
  const [customerMessage, setCustomerMessage] = useState("");
  const [quoteMessage, setQuoteMessage] = useState("");
  const [tempPassword, setTempPassword] = useState("");

  async function createCustomer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCustomerMessage("");
    setTempPassword("");
    const form = event.currentTarget;
    const data = new FormData(form);
    const response = await fetch("/api/admin/customers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.get("name"),
        email: data.get("email"),
      }),
    });
    const payload = (await response.json()) as {
      error?: string;
      password?: string;
      emailed?: boolean;
    };
    if (!response.ok) {
      setCustomerMessage(payload.error || "Could not create the customer.");
      return;
    }
    form.reset();
    if (payload.password) {
      setTempPassword(payload.password);
      setCustomerMessage(
        "Customer saved. Email could not be sent — share this one-time password.",
      );
    } else {
      setCustomerMessage("Customer saved. A login note was emailed.");
    }
    window.setTimeout(() => window.location.reload(), 1200);
  }

  async function createQuote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuoteMessage("");
    const form = event.currentTarget;
    const data = new FormData(form);
    const amountCents = Math.round(Number(data.get("amount")) * 100);
    const response = await fetch("/api/admin/quotes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        customerId: data.get("customerId"),
        dueDate: data.get("dueDate"),
        billing: data.get("billing"),
        items: [
          {
            description: data.get("description"),
            quantity: 1,
            amountCents,
          },
        ],
      }),
    });
    const payload = (await response.json()) as { error?: string };
    if (!response.ok) {
      setQuoteMessage(payload.error || "Could not create the quote.");
      return;
    }
    form.reset();
    setQuoteMessage("Quote saved and marked sent.");
    window.setTimeout(() => window.location.reload(), 800);
  }

  return (
    <div className="grid gap-6 desktop:grid-cols-2">
      <form onSubmit={createCustomer} className="card-peach rounded-[18px] p-6">
        <h2 className="text-xl font-semibold text-charcoal">Create a customer</h2>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Name</span>
          <input className="field" name="name" required />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Email</span>
          <input className="field" name="email" type="email" required />
        </label>
        {customerMessage ? (
          <p className="mt-4 text-sm text-charcoal" role="status">
            {customerMessage}
          </p>
        ) : null}
        {tempPassword ? (
          <p className="mt-2 select-all text-sm font-semibold text-brand">
            {tempPassword}
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-6 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504]"
        >
          Create customer
        </button>
      </form>

      <form onSubmit={createQuote} className="card-peach rounded-[18px] p-6">
        <h2 className="text-xl font-semibold text-charcoal">Generate a quote</h2>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Customer</span>
          <select className="field" name="customerId" required>
            <option value="">Select</option>
            {customers.map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.email}
              </option>
            ))}
          </select>
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Line item</span>
          <input className="field" name="description" required />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Amount (USD)</span>
          <input
            className="field"
            name="amount"
            type="number"
            min="1"
            step="0.01"
            required
          />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Due date</span>
          <input className="field" name="dueDate" type="date" />
        </label>
        <fieldset className="mt-4 grid gap-2">
          <legend className="text-sm font-semibold text-charcoal">Billing</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="billing"
              value="one_time"
              defaultChecked
              className="size-4 accent-[#FC7004]"
            />
            One-time
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="billing"
              value="monthly"
              className="size-4 accent-[#FC7004]"
            />
            Recurring monthly
          </label>
        </fieldset>
        {quoteMessage ? (
          <p className="mt-4 text-sm text-charcoal" role="status">
            {quoteMessage}
          </p>
        ) : null}
        <button
          type="submit"
          className="mt-6 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504]"
        >
          Create quote
        </button>
      </form>

      <section className="card-peach rounded-[18px] p-6 desktop:col-span-2">
        <h2 className="text-xl font-semibold text-charcoal">Recent quotes</h2>
        {quotes.length === 0 ? (
          <p className="mt-3 text-sm text-neutral-600">No quotes yet.</p>
        ) : (
          <ul className="mt-4 grid gap-3">
            {quotes.map((quote) => {
              const service = quote.items[0]?.description ?? "Service";
              return (
                <li
                  key={quote.id}
                  className="rounded-xl border border-[#EDE4D8] bg-[#FDFDFD] px-4 py-3"
                >
                  <p className="text-sm font-semibold text-charcoal">
                    {quote.serviceAddress || quote.customerName || "Quote"}
                  </p>
                  <p className="mt-1 text-sm text-neutral-600">
                    {service} ·{" "}
                    {(quote.amountCents / 100).toLocaleString("en-US", {
                      style: "currency",
                      currency: quote.currency.toUpperCase(),
                    })}
                    {quote.billing === "monthly" ? "/month" : ""} · {quote.status}
                  </p>
                  <StatusChip className="mt-2 text-xs tracking-[0.12em]">
                    {quote.source === "sms"
                      ? `SMS ···${quote.smsFromLast4 || "----"}`
                      : "Admin"}
                  </StatusChip>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
