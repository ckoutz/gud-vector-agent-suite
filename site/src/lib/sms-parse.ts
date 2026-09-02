import { looksLikeEmail, toE164 } from "./phone";
import type { Billing } from "./store";

export type ParsedSmsQuote = {
  name: string;
  phone: string | null;
  email: string | null;
  service: string;
  amountCents: number;
  billing: Billing;
  address: string | null;
};

export type SmsNeededField = "name" | "phone" | "service" | "price";

export type SmsQuoteDraft = {
  name: string | null;
  phone: string | null;
  email: string | null;
  service: string | null;
  amountCents: number | null;
  billing: Billing | null;
  address: string | null;
  askedFor: SmsNeededField | null;
};

export type SmsExtraction = {
  name: string | null;
  phone: string | null;
  email: string | null;
  service: string | null;
  amountCents: number | null;
  billing: Billing | null;
  address: string | null;
};

const MONTHLY =
  /\b(monthly|per\s*month|a\s*month|\/\s*month|\/mo|\bmo\b)\b/i;

const SERVICE_PHRASES = [
  "website + systems setup",
  "website and systems setup",
  "website building",
  "systems setup",
  "lawn care",
  "lawncare",
  "garden care",
  "yard care",
  "pressure washing",
  "window washing",
  "window cleaning",
  "gutter cleaning",
  "pest control",
  "tree trimming",
  "junk hauling",
  "house cleaning",
].sort((a, b) => b.length - a.length);

const SERVICE_START = new Set([
  "lawn",
  "lawncare",
  "garden",
  "yard",
  "mowing",
  "mow",
  "landscaping",
  "landscape",
  "website",
  "web",
  "site",
  "systems",
  "automation",
  "cleaning",
  "clean",
  "plumbing",
  "electrical",
  "hvac",
  "roof",
  "roofing",
  "painting",
  "paint",
  "pest",
  "window",
  "pressure",
  "irrigation",
  "tree",
  "hauling",
  "junk",
  "gutter",
  "fence",
  "fencing",
  "pool",
  "solar",
  "seo",
  "consulting",
]);

const STREET =
  /\b(\d{1,6}\s+[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,6}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|place|pl|circle|cir|parkway|pkwy)\.?)(?:\s+(?:apt|unit|#)\s*[A-Za-z0-9-]+)?\b/i;

const PHONE =
  /(?<!\d)(?:\+?1[\s.-]*)?(?:\(?\d{3}\)?[\s.-]*)\d{3}[\s.-]*\d{4}(?!\d)/g;

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;

const AMOUNT_MONTHLY =
  /\$?\s*(\d{1,6}(?:,\d{3})?(?:\.\d{1,2})?)\s*(?:\/\s*)?(?:per\s+month|a\s+month|monthly|month|mo)\b/gi;
const AMOUNT_DOLLAR = /\$\s*(\d{1,6}(?:,\d{3})?(?:\.\d{1,2})?)/g;

function clean(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

function titleName(value: string) {
  return clean(value)
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

function dollarsToCents(raw: string) {
  const dollars = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(dollars) || dollars <= 0) return null;
  return Math.round(dollars * 100);
}

function cutSpans(text: string, spans: { start: number; end: number }[]) {
  const sorted = [...spans].sort((a, b) => b.start - a.start);
  let out = text;
  for (const span of sorted) {
    if (span.start < 0 || span.end > out.length) continue;
    out = `${out.slice(0, span.start)} ${out.slice(span.end)}`;
  }
  return clean(out.replace(/[,;:]+/g, " "));
}

function matches(regex: RegExp, text: string) {
  regex.lastIndex = 0;
  return text.matchAll(regex);
}

const FILLER_NAME = new Set([
  "that's",
  "thats",
  "this",
  "that",
  "the",
  "their",
  "there",
  "a",
  "an",
  "for",
  "and",
  "to",
  "of",
  "please",
  "its",
  "it's",
  "just",
  "ok",
  "okay",
  "yes",
  "no",
  "hi",
  "hello",
  "hey",
  "thanks",
  "thank",
  "you",
]);

const GREETING =
  /^(hi|hello|hey|thanks|thank you|yo|ok|okay|yes|no|start|help)\s*[.!]?\s*$/i;

function isFillerName(value: string | null) {
  if (!value) return true;
  const tokens = value.toLowerCase().split(" ").filter(Boolean);
  return tokens.length > 0 && tokens.every((token) => FILLER_NAME.has(token.replace(/[^a-z']/g, "")));
}

function extractEmails(text: string) {
  const spans: { start: number; end: number }[] = [];
  let email: string | null = null;
  for (const match of matches(EMAIL, text)) {
    const value = match[0];
    if (!looksLikeEmail(value)) continue;
    email = value.trim().toLowerCase();
    spans.push({ start: match.index ?? 0, end: (match.index ?? 0) + value.length });
  }
  return { email, spans };
}

function extractPhones(text: string) {
  const spans: { start: number; end: number }[] = [];
  let phone: string | null = null;
  for (const match of matches(PHONE, text)) {
    const e164 = toE164(match[0]);
    if (!e164) continue;
    phone = e164;
    spans.push({
      start: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
    });
  }
  return { phone, spans };
}

export function extractAmount(text: string): {
  amountCents: number;
  billing: Billing;
  start: number;
  end: number;
} | null {
  let best: { amountCents: number; billing: Billing; start: number; end: number } | null =
    null;
  for (const match of matches(AMOUNT_MONTHLY, text)) {
    const cents = dollarsToCents(match[1]);
    if (!cents) continue;
    const start = match.index ?? 0;
    const candidate = {
      amountCents: cents,
      billing: "monthly" as const,
      start,
      end: start + match[0].length,
    };
    if (!best || start < best.start) best = candidate;
  }
  for (const match of matches(AMOUNT_DOLLAR, text)) {
    const cents = dollarsToCents(match[1]);
    if (!cents) continue;
    const start = match.index ?? 0;
    const end = start + match[0].length;
    if (best && start >= best.start && start < best.end) continue;
    const slice = text.slice(start, Math.min(text.length, end + 18));
    const billing: Billing = MONTHLY.test(slice) ? "monthly" : "one_time";
    if (!best || (best.billing !== "monthly" && start < best.start)) {
      best = { amountCents: cents, billing, start, end };
    }
  }
  if (best) return best;
  const bare = text.match(
    /^\s*(\d{1,6}(?:,\d{3})?(?:\.\d{1,2})?)\s*(?:\/\s*(?:mo|month))?\s*$/i,
  );
  if (!bare) return null;
  const cents = dollarsToCents(bare[1]);
  if (!cents) return null;
  return {
    amountCents: cents,
    billing: MONTHLY.test(text) ? "monthly" : "one_time",
    start: 0,
    end: text.length,
  };
}

function extractAddress(text: string) {
  const labeled = text.match(
    /\b(?:address|service address)\s*[:\-]\s*(.+)$/i,
  );
  if (labeled?.[1]) {
    return {
      address: clean(labeled[1]),
      spans: [
        {
          start: labeled.index ?? 0,
          end: (labeled.index ?? 0) + labeled[0].length,
        },
      ],
    };
  }
  const match = text.match(STREET);
  if (!match) return { address: null as string | null, spans: [] as { start: number; end: number }[] };
  const start = match.index ?? 0;
  return {
    address: clean(match[0]),
    spans: [{ start, end: start + match[0].length }],
  };
}

function labeledValue(text: string, labels: string[]) {
  const pattern = new RegExp(
    `\\b(?:${labels.join("|")})\\s*[:\\-]\\s*(.+)$`,
    "i",
  );
  const match = text.match(pattern);
  if (!match?.[1]) return null;
  return clean(match[1]);
}

function splitNameAndService(remainder: string): {
  name: string | null;
  service: string | null;
} {
  const text = clean(remainder);
  if (!text) return { name: null, service: null };

  const named = labeledValue(text, ["name", "customer", "client"]);
  const serviced = labeledValue(text, ["service", "job", "work"]);
  if (named || serviced) {
    return {
      name: named ? titleName(named) : null,
      service: serviced,
    };
  }

  const lower = text.toLowerCase();
  for (const phrase of SERVICE_PHRASES) {
    const index = lower.indexOf(phrase);
    if (index < 0) continue;
    const name = clean(text.slice(0, index));
    const service = clean(text.slice(index));
    const titled = name ? titleName(name) : null;
    return {
      name: isFillerName(titled) ? null : titled,
      service: service || phrase,
    };
  }

  const tokens = text.split(" ").filter(Boolean);
  const serviceAt = tokens.findIndex((token) =>
    SERVICE_START.has(token.toLowerCase().replace(/[^a-z]/g, "")),
  );
  let name: string | null = null;
  let service: string | null = null;
  if (serviceAt > 0) {
    name = titleName(tokens.slice(0, serviceAt).join(" "));
    service = clean(tokens.slice(serviceAt).join(" "));
  } else if (serviceAt === 0) {
    service = clean(text);
  } else if (tokens.length >= 4) {
    name = titleName(tokens.slice(0, 2).join(" "));
    service = clean(tokens.slice(2).join(" "));
  } else if (tokens.length === 3) {
    name = titleName(tokens.slice(0, 2).join(" "));
    service = tokens[2];
  } else {
    name = titleName(text);
  }
  return {
    name: isFillerName(name) ? null : name,
    service,
  };
}

export function emptySmsDraft(): SmsQuoteDraft {
  return {
    name: null,
    phone: null,
    email: null,
    service: null,
    amountCents: null,
    billing: null,
    address: null,
    askedFor: null,
  };
}

export function extractSmsFields(
  raw: string,
  context?: { askedFor?: SmsNeededField | null },
): SmsExtraction {
  const text = clean(raw.replace(/[“”]/g, '"'));
  const empty: SmsExtraction = {
    name: null,
    phone: null,
    email: null,
    service: null,
    amountCents: null,
    billing: null,
    address: null,
  };
  if (!text) return empty;
  if (GREETING.test(text) && !toE164(text)) return empty;

  const emails = extractEmails(text);
  const phones = extractPhones(text);
  const amount = extractAmount(text);
  const address = extractAddress(text);
  const spans = [
    ...emails.spans,
    ...phones.spans,
    ...address.spans,
    ...(amount ? [{ start: amount.start, end: amount.end }] : []),
  ];
  const remainder = cutSpans(text, spans);
  let { name, service } = splitNameAndService(remainder);
  const asked = context?.askedFor;

  if (asked === "name" && !name && remainder) {
    name = titleName(remainder);
    service = service && SERVICE_START.has(remainder.split(" ")[0]?.toLowerCase() ?? "")
      ? service
      : null;
  }
  if (asked === "service" && !service && remainder && !phones.phone && !amount) {
    service = remainder;
    if (asked === "service") name = name && name.toLowerCase() === remainder.toLowerCase() ? null : name;
  }
  if (asked === "phone" && !phones.phone && toE164(text)) {
    phones.phone = toE164(text);
  }
  if (asked === "price" && !amount) {
    const only = extractAmount(text);
    if (only) {
      return {
        ...empty,
        phone: phones.phone,
        email: emails.email,
        name,
        service,
        address: address.address,
        amountCents: only.amountCents,
        billing: only.billing,
      };
    }
  }

  return {
    name,
    phone: phones.phone,
    email: emails.email,
    service,
    address: address.address,
    amountCents: amount?.amountCents ?? null,
    billing: amount?.billing ?? null,
  };
}

function pickField(
  current: string | null,
  incoming: string | null,
  asked: boolean,
) {
  if (!incoming) return current;
  if (!current || asked) return incoming;
  return current;
}

export function mergeSmsDraft(
  draft: SmsQuoteDraft,
  extraction: SmsExtraction,
): SmsQuoteDraft {
  return {
    name: pickField(draft.name, extraction.name, draft.askedFor === "name"),
    phone: extraction.phone || draft.phone,
    email: extraction.email || draft.email,
    service: pickField(
      draft.service,
      extraction.service,
      draft.askedFor === "service",
    ),
    amountCents:
      draft.askedFor === "price"
        ? (extraction.amountCents ?? draft.amountCents)
        : (draft.amountCents ?? extraction.amountCents),
    billing:
      draft.askedFor === "price"
        ? (extraction.billing ?? draft.billing)
        : (draft.billing ?? extraction.billing),
    address: extraction.address || draft.address,
    askedFor: draft.askedFor,
  };
}

export function firstMissingSmsField(draft: SmsQuoteDraft): SmsNeededField | null {
  if (!draft.name) return "name";
  if (!draft.phone && !draft.email) return "phone";
  if (!draft.service) return "service";
  if (!draft.amountCents) return "price";
  return null;
}

export function smsAskPrompt(
  field: SmsNeededField | "address",
  draft?: Pick<SmsQuoteDraft, "service"> | null,
): string {
  if (field === "phone") return "Got it. I just need their phone number.";
  if (field === "name") return "Got it. I just need the customer's name.";
  if (field === "service") return "That's great. I just need the service.";
  if (field === "address") {
    if (draft?.service) {
      return `That's great. For ${draft.service} I just need the service address.`;
    }
    return "That's great. I just need the service address.";
  }
  if (field === "price") {
    if (draft?.service) {
      return `Got it. For ${draft.service} I just need the price.`;
    }
    return "Got it. I just need the price.";
  }
  return "Got it. I just need one more detail.";
}

export function completedSmsQuote(draft: SmsQuoteDraft): ParsedSmsQuote | null {
  if (!draft.name || !draft.service || !draft.amountCents) return null;
  if (!draft.phone && !draft.email) return null;
  return {
    name: draft.name,
    phone: draft.phone,
    email: draft.email,
    service: draft.service,
    amountCents: draft.amountCents,
    billing: draft.billing || "one_time",
    address: draft.address,
  };
}

function isCancel(text: string) {
  return /^(cancel|nevermind|never mind|stop|forget it)\s*[.!]?\s*$/i.test(text);
}

function isNewQuote(text: string) {
  return /^\s*new quote\b/i.test(text);
}

export function applySmsTurn(draft: SmsQuoteDraft, raw: string) {
  const text = clean(raw);
  if (!text) {
    const missing = firstMissingSmsField(draft) ?? "name";
    return {
      draft: { ...draft, askedFor: missing },
      complete: null as ParsedSmsQuote | null,
      cancel: false,
      prompt: smsAskPrompt(missing, draft),
    };
  }
  if (isCancel(text)) {
    return {
      draft: emptySmsDraft(),
      complete: null,
      cancel: true,
      prompt: "Okay, that quote is cancelled. Send a new one whenever you're ready.",
    };
  }
  const incoming = isNewQuote(text)
    ? clean(text.replace(/^\s*new quote\b[:,\s]*/i, ""))
    : text;
  const base = isNewQuote(text) ? emptySmsDraft() : draft;
  const extraction = extractSmsFields(incoming || text, {
    askedFor: base.askedFor,
  });
  const merged = mergeSmsDraft(base, extraction);
  return finishSmsTurn(merged);
}

export function finishSmsTurn(merged: SmsQuoteDraft) {
  const complete = completedSmsQuote(merged);
  if (complete) {
    return {
      draft: emptySmsDraft(),
      complete,
      cancel: false,
      prompt: null as string | null,
    };
  }
  const missing = firstMissingSmsField(merged) ?? "name";
  return {
    draft: { ...merged, askedFor: missing },
    complete: null as ParsedSmsQuote | null,
    cancel: false,
    prompt: smsAskPrompt(missing, merged),
  };
}

export function toSmsQuoteDraft(input: {
  name: string | null;
  phone: string | null;
  email: string | null;
  service: string | null;
  amountCents: number | null;
  billing: Billing | null;
  address: string | null;
  askedFor: SmsNeededField | null;
}): SmsQuoteDraft {
  return {
    name: input.name,
    phone: input.phone,
    email: input.email,
    service: input.service,
    amountCents: input.amountCents,
    billing: input.billing,
    address: input.address,
    askedFor: input.askedFor,
  };
}

export function parseSmsQuote(raw: string): ParsedSmsQuote | null {
  return applySmsTurn(emptySmsDraft(), raw).complete;
}

export function formatSmsAmount(cents: number, billing: Billing) {
  const dollars = (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2);
  return billing === "monthly" ? `$${dollars}/month` : `$${dollars}`;
}

export function quoteSentReply(parsed: ParsedSmsQuote) {
  const price = formatSmsAmount(parsed.amountCents, parsed.billing);
  return `Quote sent to ${parsed.name} for ${parsed.service} (${price}).`;
}
