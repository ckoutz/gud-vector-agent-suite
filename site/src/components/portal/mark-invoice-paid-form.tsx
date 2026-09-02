"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

export function MarkInvoicePaidForm({ invoiceId }: { invoiceId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const today = new Date().toISOString().slice(0, 10);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch(
      `/api/invoices/${encodeURIComponent(invoiceId)}/mark-paid`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          method: data.get("method"),
          note: data.get("note"),
          paidAt: data.get("paidAt"),
        }),
      },
    );
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not record that payment.");
      return;
    }
    router.refresh();
  }

  return (
    <form
      onSubmit={(event) => void onSubmit(event)}
      className="mt-6 border-t border-[#EDE4D8] pt-5"
    >
      <p className="text-sm font-semibold text-charcoal">
        Record cash or check
      </p>
      <p className="mt-1 text-sm text-neutral-600">
        For money collected in person. This is a record only — it does not send
        anything through Stripe.
      </p>
      <fieldset className="mt-4 grid gap-2">
        <legend className="text-sm font-semibold text-charcoal">Method</legend>
        <label className="flex items-center gap-2 text-sm text-charcoal">
          <input
            type="radio"
            name="method"
            value="cash"
            required
            className="size-4 accent-[#FC7004]"
            defaultChecked
          />
          Cash
        </label>
        <label className="flex items-center gap-2 text-sm text-charcoal">
          <input
            type="radio"
            name="method"
            value="check"
            className="size-4 accent-[#FC7004]"
          />
          Check
        </label>
      </fieldset>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">
          Date collected <span className="font-normal text-neutral-500">(optional)</span>
        </span>
        <input className="field" type="date" name="paidAt" defaultValue={today} />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">
          Note <span className="font-normal text-neutral-500">(optional)</span>
        </span>
        <textarea
          className="field min-h-24"
          name="note"
          maxLength={500}
          placeholder="Check number, who collected it, or other detail"
        />
      </label>
      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="mt-5 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {pending ? "Saving…" : "Mark paid"}
      </button>
    </form>
  );
}
