"use client";

import { useState, type FormEvent } from "react";

export function ProfileForm({
  name,
  businessName,
  email,
  phone,
}: {
  name: string;
  businessName: string;
  email: string;
  phone: string;
}) {
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    setSaved(false);
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.get("name"),
        businessName: data.get("businessName"),
        email: data.get("email"),
        phone: data.get("phone"),
      }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending(false);
    if (!response.ok) {
      setError(payload.error || "Could not save your profile.");
      return;
    }
    setSaved(true);
  }

  return (
    <form onSubmit={onSubmit} className="card-peach rounded-[18px] p-6">
      <label className="grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Name</span>
        <input
          className="field"
          name="name"
          autoComplete="name"
          defaultValue={name}
          required
        />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">
          Business name
        </span>
        <input
          className="field"
          name="businessName"
          autoComplete="organization"
          defaultValue={businessName}
        />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Email</span>
        <input
          className="field"
          name="email"
          type="email"
          autoComplete="email"
          defaultValue={email}
          required
        />
      </label>
      <label className="mt-4 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Mobile</span>
        <input
          className="field"
          name="phone"
          type="tel"
          autoComplete="tel"
          defaultValue={phone}
        />
      </label>
      {error ? (
        <p className="mt-4 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      {saved ? (
        <p className="mt-4 text-sm text-neutral-600" role="status">
          Saved.
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {pending ? "Saving…" : "Save"}
      </button>
    </form>
  );
}
