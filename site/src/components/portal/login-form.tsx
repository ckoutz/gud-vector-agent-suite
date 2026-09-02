"use client";

import { useState, type FormEvent } from "react";

import { EndUserProviders } from "@/components/portal/end-user-providers";

export function LoginForm({
  quoteToken,
  googleReady,
  phoneReady,
  errorFromServer,
}: {
  quoteToken?: string;
  googleReady: boolean;
  phoneReady: boolean;
  errorFromServer?: string;
}) {
  const [error, setError] = useState(errorFromServer || "");
  const [pending, setPending] = useState(false);
  const token = quoteToken ?? "";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: data.get("email"),
        password: data.get("password"),
        quote: token || undefined,
      }),
    });
    const payload = (await response.json()) as { error?: string };
    if (!response.ok) {
      setPending(false);
      setError(payload.error || "That login did not work.");
      return;
    }
    window.location.href = "/portal";
  }

  const signupHref = token
    ? `/portal/signup?quote=${encodeURIComponent(token)}`
    : "/portal/signup";

  return (
    <div className="grid gap-6">
      <form onSubmit={onSubmit} className="card-peach rounded-[18px] p-6">
        <h2 className="text-lg font-semibold text-charcoal">Email</h2>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Email</span>
          <input
            className="field"
            name="email"
            type="email"
            autoComplete="username"
            required
          />
        </label>
        <label className="mt-4 grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Password</span>
          <input
            className="field"
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="mt-6 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
        >
          {pending ? "Signing in…" : "Log in"}
        </button>
      </form>

      <EndUserProviders
        quoteToken={token}
        googleReady={googleReady}
        phoneReady={phoneReady}
        next="login"
      />

      {error ? (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}

      <p className="text-sm text-neutral-600">
        New here?{" "}
        <a href={signupHref} className="font-semibold text-brand">
          Create an account
        </a>
      </p>
    </div>
  );
}
