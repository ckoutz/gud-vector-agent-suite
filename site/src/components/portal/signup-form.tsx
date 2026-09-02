"use client";

import { useState, type FormEvent } from "react";

import { EndUserProviders } from "@/components/portal/end-user-providers";

export function SignupForm({
  quoteToken,
  googleReady,
  phoneReady,
  errorFromServer,
}: {
  quoteToken: string;
  googleReady: boolean;
  phoneReady: boolean;
  errorFromServer?: string;
}) {
  const [error, setError] = useState(errorFromServer || "");
  const [pending, setPending] = useState(false);

  async function onEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.get("name"),
        email: data.get("email"),
        password: data.get("password"),
        phone: data.get("phone"),
        quote: quoteToken || undefined,
      }),
    });
    const payload = (await response.json()) as { error?: string };
    if (!response.ok) {
      setPending(false);
      setError(payload.error || "Could not create that account.");
      return;
    }
    window.location.href = "/portal";
  }

  const loginHref = quoteToken
    ? `/portal/login?quote=${encodeURIComponent(quoteToken)}`
    : "/portal/login";

  return (
    <div className="grid gap-6">
      <form onSubmit={onEmail} className="card-peach rounded-[18px] p-6">
        <h2 className="text-lg font-semibold text-charcoal">Email</h2>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Name</span>
          <input className="field" name="name" autoComplete="name" />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Email</span>
          <input
            className="field"
            name="email"
            type="email"
            autoComplete="email"
            required
          />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Password</span>
          <input
            className="field"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            required
          />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">
            Mobile (optional)
          </span>
          <input
            className="field"
            name="phone"
            type="tel"
            autoComplete="tel"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
        >
          {pending ? "Creating account…" : "Create account"}
        </button>
      </form>

      <EndUserProviders
        quoteToken={quoteToken}
        googleReady={googleReady}
        phoneReady={phoneReady}
        next="signup"
      />

      {error ? (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}

      <p className="text-sm text-neutral-600">
        Already have an account?{" "}
        <a href={loginHref} className="font-semibold text-brand">
          Log in
        </a>
      </p>
    </div>
  );
}
