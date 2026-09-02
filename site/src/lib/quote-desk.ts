import type { Quote, QuoteStatus, ServiceStatus } from "@/lib/store";

export const QUOTE_EXPIRE_DAYS = 14;

export function liveQuoteLabel(status: QuoteStatus) {
  if (status === "sent" || status === "draft") return "Sent";
  if (status === "accepted") return "Accepted";
  return null;
}

export function serviceStatus(status: QuoteStatus): ServiceStatus | null {
  if (status === "paid" || status === "acknowledged") return "active";
  if (status === "paused") return "inactive";
  if (status === "canceled") return "complete";
  return null;
}

export function serviceStatusLabel(status: QuoteStatus) {
  const value = serviceStatus(status);
  if (value === "active") return "Active";
  if (value === "inactive") return "Inactive";
  if (value === "complete") return "Complete";
  return null;
}

export function isLiveQuote(quote: Quote) {
  return liveQuoteLabel(quote.status) !== null && !isExpiredSent(quote);
}

export function isCurrentCustomerQuote(quote: Quote) {
  return serviceStatus(quote.status) !== null;
}

export function isEndUserOpenQuote(quote: Quote) {
  return quote.status === "sent" || quote.status === "draft" || quote.status === "accepted";
}

export function canPauseService(quote: Quote) {
  return quote.status === "paid" || quote.status === "acknowledged";
}

export function canCancelService(quote: Quote) {
  return (
    quote.status === "paid" ||
    quote.status === "acknowledged" ||
    quote.status === "paused"
  );
}

export function sentExpiresAt(createdAt: string) {
  return new Date(
    Date.parse(createdAt) + QUOTE_EXPIRE_DAYS * 24 * 60 * 60 * 1000,
  );
}

export function isExpiredSent(quote: Quote, now = Date.now()) {
  if (quote.status !== "sent") return false;
  return now >= sentExpiresAt(quote.createdAt).getTime();
}

export function expiresInCopy(createdAt: string, now = Date.now()) {
  const ms = sentExpiresAt(createdAt).getTime() - now;
  if (ms <= 0) return "Expired";
  const days = Math.ceil(ms / (24 * 60 * 60 * 1000));
  if (days === 1) return "Expires in 1 day";
  return `Expires in ${days} days`;
}

export function quoteService(quote: Quote) {
  return quote.items[0]?.description ?? "Service";
}

export function formatMoney(cents: number, currency = "usd") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(cents / 100);
}
