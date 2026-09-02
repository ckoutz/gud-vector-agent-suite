import {
  type SmsExtraction,
  type SmsQuoteDraft,
} from "@/lib/sms-parse";
import { looksLikeEmail, toE164 } from "@/lib/phone";

function asString(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asCents(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return Math.round(value < 1000 ? value * 100 : value);
  }
  if (typeof value === "string") {
    const dollars = Number(value.replace(/[$,]/g, ""));
    if (Number.isFinite(dollars) && dollars > 0) {
      return Math.round(dollars * 100);
    }
  }
  return null;
}

export async function llmExtractSms(
  raw: string,
  draft: SmsQuoteDraft,
): Promise<SmsExtraction | null> {
  const key = process.env.OPENAI_API_KEY?.trim();
  if (!key) return null;
  if (draft.name && draft.service && (draft.phone || draft.email) && draft.amountCents) {
    return null;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      signal: controller.signal,
      body: JSON.stringify({
        model: process.env.OPENAI_SMS_MODEL?.trim() || "gpt-4o-mini",
        temperature: 0,
        response_format: { type: "json_object" },
        messages: [
          {
            role: "system",
            content:
              "Extract quote fields from a shop owner's SMS. Return JSON keys: name, phone, email, service, amountCents (integer USD cents), billing (monthly or one_time), address. Use null when unknown. Do not invent. Name is a person, not a service. Service is the work (for example lawn care).",
          },
          {
            role: "user",
            content: JSON.stringify({
              text: raw,
              already: {
                name: draft.name,
                phone: draft.phone,
                email: draft.email,
                service: draft.service,
                amountCents: draft.amountCents,
                billing: draft.billing,
                address: draft.address,
              },
            }),
          },
        ],
      }),
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    const content = payload.choices?.[0]?.message?.content;
    if (!content) return null;
    const parsed = JSON.parse(content) as Record<string, unknown>;
    const phone = toE164(asString(parsed.phone));
    const email = asString(parsed.email);
    return {
      name: asString(parsed.name),
      phone,
      email: email && looksLikeEmail(email) ? email.toLowerCase() : null,
      service: asString(parsed.service),
      amountCents: asCents(parsed.amountCents ?? parsed.amount),
      billing:
        parsed.billing === "monthly" || parsed.billing === "one_time"
          ? parsed.billing
          : null,
      address: asString(parsed.address),
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
