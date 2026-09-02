import type {
  ClientInvoice,
  ClientInvoiceStatus,
  InvoicePaidMethod,
} from "@/lib/store";

export type { InvoicePaidMethod };

export type InvoiceStatusLabel = "Due" | "Paid (card)" | "Paid (cash/check)";

export function invoiceStatusLabel(invoice: {
  status: ClientInvoiceStatus | string;
  paidMethod?: InvoicePaidMethod | null;
}): InvoiceStatusLabel {
  if (invoice.status === "paid") {
    if (invoice.paidMethod === "cash" || invoice.paidMethod === "check") {
      return "Paid (cash/check)";
    }
    return "Paid (card)";
  }
  return "Due";
}

export function paidMethodCopy(method: InvoicePaidMethod | null | undefined) {
  if (method === "card") return "Card";
  if (method === "cash") return "Cash";
  if (method === "check") return "Check";
  return null;
}

export function isDueInvoice(invoice: Pick<ClientInvoice, "status">) {
  return invoice.status === "open" || invoice.status === "draft";
}

export function invoicePath(stripeInvoiceId: string) {
  return `/portal/invoices/${encodeURIComponent(stripeInvoiceId)}`;
}
