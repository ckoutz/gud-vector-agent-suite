"use client";

import { useState, type FormEvent } from "react";

import { site } from "@/lib/site";

export function ContactForm() {
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">(
    "idle",
  );
  const [error, setError] = useState("");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("submitting");
    setError("");
    const form = event.currentTarget;
    const data = new FormData(form);

    try {
      const response = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: data.get("name"),
          email: data.get("email"),
          businessName: data.get("businessName"),
          need: data.get("need"),
          message: data.get("message"),
          website: data.get("website"),
        }),
      });
      const payload = (await response.json()) as { error?: string };
      if (!response.ok) {
        setStatus("error");
        setError(
          payload.error ||
            "That didn’t go through. Try again, or copy the email on this page.",
        );
        return;
      }
      setStatus("success");
      form.reset();
    } catch {
      setStatus("error");
      setError("The form could not reach the server. Try again in a moment.");
    }
  }

  if (status === "success") {
    return (
      <div
        className="card-peach rounded-[18px] bg-white p-6"
        style={{ backgroundColor: "#ffffff" }}
      >
        <p className="text-xl font-semibold text-charcoal">Got it.</p>
        <p className="mt-2 text-base leading-relaxed text-neutral-600">
          We’ll reply at the email you left.
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="relative card-peach rounded-[18px] bg-white p-6"
      style={{ backgroundColor: "#ffffff" }}
    >
      <div className="grid gap-4">
        <label className="grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Name</span>
          <input
            className="field"
            name="name"
            autoComplete="name"
            required
            maxLength={120}
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Email</span>
          <input
            className="field"
            name="email"
            type="email"
            autoComplete="email"
            required
            maxLength={200}
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">
            Business name <span className="font-normal text-neutral-500">(optional)</span>
          </span>
          <input
            className="field"
            name="businessName"
            autoComplete="organization"
            maxLength={160}
          />
        </label>
        <fieldset className="grid gap-2">
          <legend className="text-sm font-semibold text-charcoal">Need</legend>
          {site.needs.map((need) => (
            <label key={need} className="flex items-center gap-2 text-sm text-charcoal">
              <input
                type="radio"
                name="need"
                value={need}
                required
                className="size-4 accent-[#FC7004]"
              />
              {need}
            </label>
          ))}
        </fieldset>
        <label className="grid gap-1.5">
          <span className="text-sm font-semibold text-charcoal">Message</span>
          <textarea
            className="field min-h-32"
            name="message"
            required
            maxLength={5000}
          />
        </label>
        <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
          <label>
            Website
            <input name="website" tabIndex={-1} autoComplete="off" />
          </label>
        </div>
      </div>
      {error ? (
        <p className="mt-4 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={status === "submitting"}
        className="mt-6 inline-flex h-11 items-center justify-center rounded-full bg-brand px-5 text-[0.95rem] font-medium text-white hover:bg-[#e56504] disabled:opacity-60"
      >
        {status === "submitting" ? "Sending…" : "Send"}
      </button>
    </form>
  );
}
