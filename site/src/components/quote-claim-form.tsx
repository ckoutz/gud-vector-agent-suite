"use client";

import { useState, type FormEvent } from "react";

export function ClaimSetupForm({
  token,
  defaultPhone,
}: {
  token: string;
  defaultPhone?: string | null;
}) {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    setMessage("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/quotes/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token,
        name: data.get("name"),
        email: data.get("email"),
        phone: data.get("phone"),
      }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not save those details.");
      return;
    }
    setMessage("Saved. You can accept or pay below.");
  }

  return (
    <form onSubmit={onSubmit} className="card-peach mt-6 rounded-[18px] p-5">
      <h2 className="text-lg font-semibold text-charcoal">Set up service</h2>
      <p className="mt-1 text-sm text-neutral-600">
        Name, email, and phone are optional, but they help the shop reach you.
      </p>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Name</span>
        <input className="field" name="name" autoComplete="name" />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Email</span>
        <input className="field" name="email" type="email" autoComplete="email" />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Phone</span>
        <input
          className="field"
          name="phone"
          type="tel"
          autoComplete="tel"
          defaultValue={defaultPhone ?? ""}
        />
      </label>
      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <p className="mt-3 text-sm text-charcoal" role="status">
          {message}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="mt-5 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {pending ? "Saving…" : "Save details"}
      </button>
    </form>
  );
}
