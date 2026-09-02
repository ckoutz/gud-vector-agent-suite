import { NextResponse } from "next/server";

import { requireAdmin } from "@/lib/api-auth";
import { sendEmail } from "@/lib/mail";
import { site } from "@/lib/site";
import {
  createQuote,
  getCustomerById,
  type Billing,
} from "@/lib/store";

type ItemInput = {
  description?: string;
  quantity?: number;
  amountCents?: number;
};

export async function POST(request: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  let body: {
    customerId?: string;
    dueDate?: string;
    billing?: Billing;
    items?: ItemInput[];
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const customer = body.customerId
    ? await getCustomerById(body.customerId)
    : null;
  if (!customer) {
    return NextResponse.json({ error: "Customer not found." }, { status: 404 });
  }

  const items = (body.items ?? [])
    .map((item) => ({
      description: item.description?.trim() ?? "",
      quantity: Number(item.quantity) || 1,
      amountCents: Number(item.amountCents) || 0,
    }))
    .filter((item) => item.description && item.amountCents > 0);

  if (items.length === 0) {
    return NextResponse.json(
      { error: "Add at least one line item with an amount." },
      { status: 400 },
    );
  }

  const amountCents = items.reduce(
    (sum, item) => sum + item.amountCents * item.quantity,
    0,
  );
  const billing: Billing = body.billing === "monthly" ? "monthly" : "one_time";
  const dueDate = body.dueDate?.trim() || null;

  const quote = await createQuote({
    customerId: customer.id,
    amountCents,
    dueDate,
    billing,
    items,
  });

  await sendEmail({
    to: customer.email,
    subject: "A quote is waiting in your Güd Vector portal",
    text: [
      `A quote is ready at ${site.url}/portal/quotes/${quote.id}`,
      `Total: ${(amountCents / 100).toFixed(2)} USD`,
      billing === "monthly" ? "This quote is billed monthly." : "This quote is a one-time payment.",
      "Log in to review it, pay, or manage billing.",
    ].join("\n"),
  });

  return NextResponse.json({ ok: true, quoteId: quote.id });
}
