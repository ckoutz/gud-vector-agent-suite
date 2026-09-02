import { validateRequest } from "twilio/lib/webhooks/webhooks";

import { notifyNewClientQuote } from "@/lib/notify";
import { rateLimit } from "@/lib/rate-limit";
import { llmExtractSms } from "@/lib/sms-llm";
import {
  applySmsTurn,
  completedSmsQuote,
  emptySmsDraft,
  finishSmsTurn,
  mergeSmsDraft,
  quoteSentReply,
  toSmsQuoteDraft,
  type ParsedSmsQuote,
  type SmsQuoteDraft,
} from "@/lib/sms-parse";
import {
  appendSmsThread,
  clearSmsDraft,
  createQuote,
  findOrCreateEndUser,
  getSmsDraft,
  saveSmsDraft,
  type StoredSmsDraft,
} from "@/lib/store";
import { twilioAccountReady } from "@/lib/twilio-env";
import { ensureIncomingSmsWebhook } from "@/lib/twilio-webhook";
import { appUrl, quoteSignupUrl } from "@/lib/urls";

export const runtime = "nodejs";

function twiml(message: string) {
  const escaped = message
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  return `<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escaped}</Message></Response>`;
}

function xml(status: number, message: string) {
  return new Response(twiml(message), {
    status,
    headers: { "Content-Type": "text/xml; charset=utf-8" },
  });
}

function last4(from: string) {
  const digits = from.replace(/\D/g, "");
  return digits.slice(-4) || null;
}

function allowedSender(from: string) {
  const raw = process.env.SMS_ALLOWED_FROM?.trim();
  if (!raw) return true;
  const incoming = from.replace(/\D/g, "");
  return raw.split(",").some((entry) => {
    const allowed = entry.trim().replace(/\D/g, "");
    return allowed.length > 0 && (incoming === allowed || incoming.endsWith(allowed));
  });
}

function webhookUrls(request: Request) {
  const urls = new Set<string>();
  const host = request.headers.get("host");
  const proto = request.headers.get("x-forwarded-proto") || "https";
  if (host) urls.add(`${proto}://${host}/api/sms/inbound`);
  urls.add(`${appUrl()}/api/sms/inbound`);
  return [...urls];
}

function validSignature(
  request: Request,
  params: Record<string, string>,
  signature: string,
  authToken: string,
) {
  return webhookUrls(request).some((url) => {
    try {
      return validateRequest(authToken, signature, url, params);
    } catch {
      return false;
    }
  });
}

async function readParams(request: Request): Promise<Record<string, string>> {
  const contentType = request.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const body = (await request.json()) as Record<string, unknown>;
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(body)) {
      if (typeof value === "string") params[key] = value;
    }
    return params;
  }
  const form = await request.formData();
  const params: Record<string, string> = {};
  for (const [key, value] of form.entries()) {
    if (typeof value === "string") params[key] = value;
  }
  return params;
}

function draftFromStored(stored: StoredSmsDraft): SmsQuoteDraft {
  return toSmsQuoteDraft({
    name: stored.name,
    phone: stored.phone,
    email: stored.email,
    service: stored.service,
    amountCents: stored.amountCents,
    billing: stored.billing,
    address: stored.address,
    askedFor: stored.askedFor,
  });
}

async function persistOpenDraft(
  from: string,
  draft: SmsQuoteDraft,
  thread: StoredSmsDraft["thread"],
) {
  await saveSmsDraft(from, {
    name: draft.name,
    phone: draft.phone,
    email: draft.email,
    service: draft.service,
    amountCents: draft.amountCents,
    billing: draft.billing,
    address: draft.address,
    askedFor: draft.askedFor,
    thread,
  });
}

async function createSentQuote(from: string, parsed: ParsedSmsQuote) {
  const endUser = await findOrCreateEndUser({
    name: parsed.name,
    email: parsed.email,
    phone: parsed.phone,
  });
  const quote = await createQuote({
    customerId: endUser?.id ?? null,
    amountCents: parsed.amountCents,
    billing: parsed.billing,
    status: "sent",
    customerName: parsed.name,
    customerPhone: parsed.phone,
    customerEmail: parsed.email,
    serviceAddress: parsed.address,
    source: "sms",
    smsFromLast4: last4(from),
    items: [
      {
        description: parsed.service,
        quantity: 1,
        amountCents: parsed.amountCents,
      },
    ],
  });
  const signupUrl = quoteSignupUrl(quote.claimToken);
  const notified = await notifyNewClientQuote({
    email: parsed.email,
    phone: parsed.phone,
    signupUrl,
    service: parsed.service,
  });
  const confirm = quoteSentReply(parsed);
  if (notified.smsOk || notified.emailed) return confirm;
  return `${confirm} We couldn't reach them yet — send them this link: ${signupUrl}`;
}

export async function POST(request: Request) {
  void ensureIncomingSmsWebhook();

  let params: Record<string, string> = {};
  try {
    params = await readParams(request);
  } catch {
    return xml(400, "Could not read that text.");
  }

  const authToken = process.env.TWILIO_AUTH_TOKEN;
  if (twilioAccountReady() && authToken) {
    const signature = request.headers.get("x-twilio-signature") ?? "";
    if (!signature || !validSignature(request, params, signature, authToken)) {
      return xml(403, "Could not verify that text.");
    }
  }

  const from = params.From ?? params.from ?? "";
  const body = params.Body ?? params.body ?? "";
  const messageSid = params.MessageSid ?? "";

  if (!rateLimit(`sms:${from || "unknown"}`, 20, 10 * 60 * 1000)) {
    return xml(429, "Too many texts. Wait a few minutes.");
  }

  if (from && !allowedSender(from)) {
    return xml(200, "This number isn’t set up for quoting.");
  }

  const stored = from ? await getSmsDraft(from) : null;
  const draft = stored ? draftFromStored(stored) : emptySmsDraft();
  let thread = stored?.thread ?? [];
  thread = appendSmsThread(thread, "in", body);

  let result = applySmsTurn(draft, body);

  if (!result.complete && !result.cancel) {
    const extra = await llmExtractSms(body, result.draft);
    if (extra) {
      result = finishSmsTurn(mergeSmsDraft(result.draft, extra));
    }
  }

  try {
    if (result.cancel) {
      if (from) await clearSmsDraft(from);
      console.info("sms.parse", { messageSid, parseOk: false, cancelled: true });
      return xml(200, result.prompt ?? "Okay, that quote is cancelled.");
    }

    if (result.complete) {
      if (from) await clearSmsDraft(from);
      const reply = await createSentQuote(from, result.complete);
      console.info("sms.parse", { messageSid, parseOk: true });
      return xml(200, reply);
    }

    const stillComplete = completedSmsQuote(result.draft);
    if (stillComplete) {
      if (from) await clearSmsDraft(from);
      const reply = await createSentQuote(from, stillComplete);
      console.info("sms.parse", { messageSid, parseOk: true });
      return xml(200, reply);
    }

    if (from) {
      await persistOpenDraft(from, result.draft, appendSmsThread(thread, "out", result.prompt ?? ""));
    }
    console.info("sms.parse", { messageSid, parseOk: false });
    return xml(
      200,
      result.prompt ?? "Got it. I just need one more detail.",
    );
  } catch {
    console.info("sms.parse", { messageSid, parseOk: true, stored: false });
    return xml(200, "Could not save that quote. Try again."    );
  }
}
