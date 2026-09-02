"use client";

import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import { useRouter } from "next/navigation";
import { FormEvent, useMemo, useState } from "react";

const stripePromises = new Map<string, Promise<Stripe | null>>();

function stripePromiseFor(publishableKey: string) {
  let promise = stripePromises.get(publishableKey);
  if (!promise) {
    promise = loadStripe(publishableKey);
    stripePromises.set(publishableKey, promise);
  }
  return promise;
}

const elementAppearance = {
  theme: "stripe" as const,
  variables: {
    colorPrimary: "#fc7004",
    colorBackground: "#FDFDFD",
    colorText: "#2c2c2c",
    colorDanger: "#b42318",
    fontFamily: "Nunito, ui-sans-serif, system-ui, sans-serif",
    borderRadius: "12px",
    spacingUnit: "4px",
  },
  rules: {
    ".Input": {
      backgroundColor: "#FDFDFD",
      borderColor: "#EDE4D8",
    },
    ".Input:focus": {
      borderColor: "#fc7004",
      boxShadow: "0 0 0 3px rgb(252 112 4 / 0.18)",
    },
    ".Tab": {
      backgroundColor: "#ffffff",
      borderColor: "#EDE4D8",
    },
    ".Tab--selected": {
      backgroundColor: "#FDFDFD",
      borderColor: "#fc7004",
      color: "#fc7004",
    },
    ".Label": {
      color: "#2c2c2c",
    },
  },
};

function InvoicePaymentForm({ onPaid }: { onPaid: () => void }) {
  const stripe = useStripe();
  const elements = useElements();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!stripe || !elements || pending) return;
    setPending(true);
    setError("");
    const result = await stripe.confirmPayment({
      elements,
      confirmParams: {
        return_url: `${window.location.origin}/portal/payment`,
      },
      redirect: "if_required",
    });
    if (result.error) {
      setPending(false);
      setError(result.error.message || "Payment did not go through.");
      return;
    }
    const status = result.paymentIntent?.status;
    if (status === "succeeded" || status === "processing") {
      onPaid();
      return;
    }
    setPending(false);
    setError("Payment did not go through.");
  }

  return (
    <form onSubmit={submit} className="mt-4">
      <PaymentElement
        options={{
          layout: "tabs",
          wallets: { applePay: "auto", googlePay: "auto" },
        }}
      />
      <button
        type="submit"
        disabled={!stripe || pending}
        className="mt-4 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {pending ? "Paying…" : "Pay"}
      </button>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

export function PayOnSiteButton({
  quoteId,
  invoiceId,
  paymentsReady,
}: {
  quoteId?: string | null;
  invoiceId?: string | null;
  paymentsReady: boolean;
}) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [clientSecret, setClientSecret] = useState("");
  const [publishableKey, setPublishableKey] = useState("");
  const [paidHere, setPaidHere] = useState(false);

  const stripePromise = useMemo(
    () => (publishableKey ? stripePromiseFor(publishableKey) : null),
    [publishableKey],
  );

  if (!paymentsReady) {
    return (
      <p className="text-bubble rounded-xl px-4 py-3 text-sm">
        Payments are being connected.
      </p>
    );
  }

  if (paidHere) {
    return <p className="text-sm text-neutral-600">Paid (card).</p>;
  }

  if (clientSecret && stripePromise) {
    return (
      <Elements
        stripe={stripePromise}
        options={{ clientSecret, appearance: elementAppearance }}
      >
        <InvoicePaymentForm
          onPaid={() => {
            setPaidHere(true);
            router.refresh();
          }}
        />
      </Elements>
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
          const response = await fetch("/api/stripe/gud-pay", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ quoteId, invoiceId }),
          });
          const payload = (await response.json()) as {
            clientSecret?: string;
            publishableKey?: string;
            alreadyPaid?: boolean;
            error?: string;
          };
          if (payload.alreadyPaid) {
            setPaidHere(true);
            router.refresh();
            return;
          }
          if (payload.clientSecret && payload.publishableKey) {
            setPublishableKey(payload.publishableKey);
            setClientSecret(payload.clientSecret);
            setPending(false);
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
