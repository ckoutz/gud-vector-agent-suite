"use client";

import { useState } from "react";

export function PayButton({
  quoteId,
  claimToken,
  paymentsReady,
}: {
  quoteId: string;
  claimToken?: string;
  paymentsReady: boolean;
}) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  if (!paymentsReady) {
    return (
      <p className="text-bubble rounded-xl px-4 py-3 text-sm">
        Payments are being connected.
      </p>
    );
  }

  return (
    <div>
      <button
        type="button"
        disabled={pending}
        className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
        onClick={async () => {
          setPending(true);
          setError("");
          const response = await fetch("/api/stripe/checkout", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(
              claimToken ? { claimToken } : { quoteId },
            ),
          });
          const payload = (await response.json()) as {
            url?: string;
            error?: string;
          };
          if (payload.url) {
            window.location.href = payload.url;
            return;
          }
          setPending(false);
          setError(payload.error || "Payments are being connected.");
        }}
      >
        {pending ? "Opening…" : "Pay"}
      </button>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function ManageBillingButton({
  paymentsReady,
  claimToken,
}: {
  paymentsReady: boolean;
  claimToken?: string;
}) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  if (!paymentsReady) {
    return (
      <p className="text-sm text-neutral-600">Payments are being connected.</p>
    );
  }

  return (
    <div>
      <button
        type="button"
        disabled={pending}
        className="inline-flex h-11 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
        onClick={async () => {
          setPending(true);
          setError("");
          const response = await fetch("/api/stripe/portal", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(claimToken ? { claimToken } : {}),
          });
          const payload = (await response.json()) as {
            url?: string;
            error?: string;
          };
          if (payload.url) {
            window.location.href = payload.url;
            return;
          }
          setPending(false);
          setError(payload.error || "Payments are being connected.");
        }}
      >
        {pending ? "Opening…" : "Manage billing"}
      </button>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
