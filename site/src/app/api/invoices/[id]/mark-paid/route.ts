import { NextResponse } from "next/server";

import { requireShop } from "@/lib/api-auth";
import { markInvoicePaidInPerson, getInvoiceByStripeId } from "@/lib/store";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { error } = await requireShop();
  if (error) return error;

  const { id } = await context.params;
  const invoiceId = decodeURIComponent(id);
  const invoice = await getInvoiceByStripeId(invoiceId);
  if (!invoice) {
    return NextResponse.json({ error: "Invoice not found." }, { status: 404 });
  }
  if (invoice.status === "paid") {
    return NextResponse.json(
      { error: "This invoice is already paid." },
      { status: 409 },
    );
  }

  let body: { method?: string; note?: string; paidAt?: string };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const method = body.method === "check" ? "check" : body.method === "cash" ? "cash" : null;
  if (!method) {
    return NextResponse.json(
      { error: "Choose cash or check." },
      { status: 400 },
    );
  }

  let paidAt: string | null = null;
  if (body.paidAt?.trim()) {
    const date = body.paidAt.trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return NextResponse.json({ error: "Use a valid date." }, { status: 400 });
    }
    paidAt = `${date}T12:00:00.000Z`;
  }

  const note = body.note?.trim() || null;
  if (note && note.length > 500) {
    return NextResponse.json(
      { error: "Keep the note under 500 characters." },
      { status: 400 },
    );
  }

  const updated = await markInvoicePaidInPerson(invoiceId, {
    method,
    note,
    paidAt,
  });
  return NextResponse.json({ ok: true, status: updated?.status ?? "paid" });
}
