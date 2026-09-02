"use client";

import { useState, type FormEvent } from "react";

export function EndUserProviders({
  quoteToken,
  googleReady,
  phoneReady,
  next,
}: {
  quoteToken: string;
  googleReady: boolean;
  phoneReady: boolean;
  next: "login" | "signup";
}) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [phoneStep, setPhoneStep] = useState<"enter" | "code" | "identity">(
    "enter",
  );
  const [phone, setPhone] = useState("");
  const [verifyToken, setVerifyToken] = useState("");
  const [prefill, setPrefill] = useState({
    name: "",
    businessName: "",
    email: "",
  });

  if (!googleReady && !phoneReady) return null;

  async function sendPhoneCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("phone-start");
    setError("");
    const data = new FormData(event.currentTarget);
    const value = String(data.get("mobile") || "");
    setPhone(value);
    const response = await fetch("/api/auth/phone/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: value }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending("");
    if (!response.ok) {
      setError(payload.error || "Could not send a code.");
      return;
    }
    setPhoneStep("code");
  }

  async function checkPhone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("phone-check");
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/phone/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone,
        code: data.get("code"),
        quote: quoteToken || undefined,
      }),
    });
    const payload = (await response.json()) as {
      error?: string;
      needsIdentity?: boolean;
      token?: string;
      name?: string;
      businessName?: string;
      email?: string;
    };
    if (!response.ok) {
      setPending("");
      setError(payload.error || "That code did not match.");
      return;
    }
    if (payload.needsIdentity && payload.token) {
      setVerifyToken(payload.token);
      setPrefill({
        name: payload.name || "",
        businessName: payload.businessName || "",
        email: payload.email || "",
      });
      setPending("");
      setPhoneStep("identity");
      return;
    }
    window.location.href = "/portal";
  }

  async function completePhone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("phone-complete");
    setError("");
    const data = new FormData(event.currentTarget);
    const response = await fetch("/api/auth/phone/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: verifyToken,
        name: data.get("name"),
        businessName: data.get("businessName"),
        email: data.get("email"),
      }),
    });
    const payload = (await response.json()) as { error?: string };
    if (!response.ok) {
      setPending("");
      setError(payload.error || "Could not finish phone signup.");
      return;
    }
    window.location.href = "/portal";
  }

  const googleHref = `/api/auth/google?next=${next}${
    quoteToken ? `&quote=${encodeURIComponent(quoteToken)}` : ""
  }`;

  return (
    <div className="grid gap-6">
      {phoneReady ? (
        <div className="card-peach rounded-[18px] p-6">
          <h2 className="text-lg font-semibold text-charcoal">Phone number</h2>
          {phoneStep === "enter" ? (
            <>
              <p className="mt-2 text-sm text-neutral-600">
                We’ll text a 6-digit Verify code. US numbers only.
              </p>
              <form
                onSubmit={(event) => void sendPhoneCode(event)}
                className="mt-4"
              >
                <label className="grid gap-1.5">
                  <span className="text-sm font-semibold text-charcoal">
                    Mobile
                  </span>
                  <input
                    className="field"
                    name="mobile"
                    type="tel"
                    autoComplete="tel"
                    inputMode="tel"
                    required
                  />
                </label>
                <button
                  type="submit"
                  disabled={Boolean(pending)}
                  className="mt-4 inline-flex h-11 w-full items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
                >
                  {pending === "phone-start" ? "Sending…" : "Text me a code"}
                </button>
              </form>
            </>
          ) : null}
          {phoneStep === "code" ? (
            <>
              <p className="mt-2 text-sm text-neutral-600">
                Enter the 6-digit code we texted.
              </p>
              <form
                onSubmit={(event) => void checkPhone(event)}
                className="mt-4"
              >
                <label className="grid gap-1.5">
                  <span className="text-sm font-semibold text-charcoal">
                    6-digit code
                  </span>
                  <input
                    className="field"
                    name="code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    required
                    minLength={6}
                    maxLength={10}
                  />
                </label>
                <button
                  type="submit"
                  disabled={Boolean(pending)}
                  className="mt-4 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
                >
                  {pending === "phone-check" ? "Checking…" : "Verify code"}
                </button>
              </form>
            </>
          ) : null}
          {phoneStep === "identity" ? (
            <>
              <p className="mt-2 text-sm text-neutral-600">
                Name, business name, and email are required to open your
                portal.
              </p>
              <form
                onSubmit={(event) => void completePhone(event)}
                className="mt-4"
              >
                <label className="grid gap-1.5">
                  <span className="text-sm font-semibold text-charcoal">
                    Name
                  </span>
                  <input
                    className="field"
                    name="name"
                    autoComplete="name"
                    defaultValue={prefill.name}
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
                    defaultValue={prefill.businessName}
                    required
                  />
                </label>
                <label className="mt-4 grid gap-1.5">
                  <span className="text-sm font-semibold text-charcoal">
                    Email
                  </span>
                  <input
                    className="field"
                    name="email"
                    type="email"
                    autoComplete="email"
                    defaultValue={prefill.email}
                    required
                  />
                </label>
                <button
                  type="submit"
                  disabled={Boolean(pending)}
                  className="mt-4 inline-flex h-11 w-full items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
                >
                  {pending === "phone-complete"
                    ? "Saving…"
                    : "Save and continue"}
                </button>
              </form>
            </>
          ) : null}
        </div>
      ) : null}

      {googleReady ? (
        <div className="card-peach rounded-[18px] p-6">
          <h2 className="text-lg font-semibold text-charcoal">Google</h2>
          <a
            href={googleHref}
            className="mt-4 inline-flex h-11 w-full items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50"
          >
            Continue with Google
          </a>
        </div>
      ) : null}

      {error ? (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
