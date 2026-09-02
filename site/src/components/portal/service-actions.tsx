"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { canCancelService, canPauseService } from "@/lib/quote-desk";
import type { Quote } from "@/lib/store";

export function ServiceActions({ quote }: { quote: Quote }) {
  const router = useRouter();
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const pauseReady = canPauseService(quote);
  const cancelReady = canCancelService(quote);

  async function act(path: "pause" | "cancel") {
    if (pending) return;
    setPending(path);
    setError("");
    const response = await fetch(`/api/quotes/${quote.id}/${path}`, {
      method: "POST",
    });
    const payload = (await response.json()) as { error?: string };
    setPending("");
    if (!response.ok) {
      setError(payload.error || "Could not update this service.");
      return;
    }
    router.refresh();
  }

  if (!pauseReady && !cancelReady) return null;

  return (
    <div className="mt-5">
      <div className="flex flex-wrap gap-2">
        {pauseReady ? (
          <button
            type="button"
            disabled={Boolean(pending)}
            onClick={() => void act("pause")}
            className="inline-flex h-11 items-center justify-center rounded-full border border-neutral-300 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-charcoal hover:bg-neutral-50 disabled:opacity-60"
          >
            {pending === "pause" ? "Saving…" : "Pause"}
          </button>
        ) : null}
        {cancelReady ? (
          <button
            type="button"
            disabled={Boolean(pending)}
            onClick={() => void act("cancel")}
            className="inline-flex h-11 items-center justify-center rounded-full border border-red-200 bg-[#FDFDFD] px-5 text-[0.95rem] font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
          >
            {pending === "cancel" ? "Saving…" : "Cancel"}
          </button>
        ) : null}
      </div>
      {error ? (
        <p className="mt-2 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
