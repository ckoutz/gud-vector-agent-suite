"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AcceptQuoteButton({ token }: { token: string }) {
  const router = useRouter();
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");

  async function act(path: "accept" | "reject") {
    setPending(path);
    setError("");
    const response = await fetch(`/api/quotes/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const payload = (await response.json()) as { error?: string };
    setPending("");
    if (!response.ok) {
      setError(payload.error || "Could not update this quote.");
      return;
    }
    if (path === "accept") {
      router.push("/portal/login");
      return;
    }
    router.refresh();
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={Boolean(pending)}
          className="inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
          onClick={() => void act("accept")}
        >
          {pending === "accept" ? "Saving…" : "Accept"}
        </button>
        <button
          type="button"
          disabled={Boolean(pending)}
          className="inline-flex h-11 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
          onClick={() => void act("reject")}
        >
          {pending === "reject" ? "Saving…" : "Reject"}
        </button>
      </div>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
