"use client";

import { useState, type FormEvent } from "react";

export function AdminLoginForm() {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: data.get("password") }),
    });
    const payload = (await response.json()) as { error?: string };
    if (!response.ok) {
      setPending(false);
      setError(payload.error || "That password did not match.");
      return;
    }
    window.location.reload();
  }

  return (
    <form onSubmit={onSubmit} className="card-peach mx-auto max-w-md rounded-[18px] p-6">
      <h1 className="text-2xl font-semibold text-charcoal">Admin</h1>
      <p className="mt-2 text-sm text-neutral-600">
        Password-gated workspace for quotes. No public directory.
      </p>
      <label className="mt-6 grid gap-1.5">
        <span className="text-sm font-semibold text-charcoal">Password</span>
        <input
          className="field"
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>
      {error ? (
        <p className="mt-4 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {pending ? "Checking…" : "Continue"}
      </button>
    </form>
  );
}
