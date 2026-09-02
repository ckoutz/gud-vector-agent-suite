import { NextResponse } from "next/server";

import { requireCustomer } from "@/lib/api-auth";
import { createSession } from "@/lib/auth";
import { isPendingEmail, looksLikeEmail, toE164 } from "@/lib/phone";
import {
  getCustomerByEmail,
  getCustomerById,
  getCustomerByPhone,
  updateCustomer,
} from "@/lib/store";

export async function POST(request: Request) {
  const { session, error } = await requireCustomer();
  if (error || !session) return error;
  if (session.role === "admin") {
    return NextResponse.json(
      { error: "Shop accounts do not use customer Profile." },
      { status: 403 },
    );
  }

  let body: {
    name?: string;
    businessName?: string;
    email?: string;
    phone?: string;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const name = body.name?.trim() || "";
  const businessName = body.businessName?.trim() || "";
  const email = body.email?.trim().toLowerCase() || "";
  const phone = toE164(body.phone);

  if (!name) {
    return NextResponse.json({ error: "Name is required." }, { status: 400 });
  }
  if (!email || !looksLikeEmail(email) || email.endsWith("@pending.invalid")) {
    return NextResponse.json({ error: "Enter a valid email." }, { status: 400 });
  }

  const customer = await getCustomerById(session.customerId);
  if (!customer || customer.role === "admin") {
    return NextResponse.json({ error: "Please log in." }, { status: 401 });
  }

  if (email !== customer.email.toLowerCase()) {
    const taken = await getCustomerByEmail(email);
    if (taken && taken.id !== customer.id) {
      return NextResponse.json(
        { error: "That email already has an account." },
        { status: 409 },
      );
    }
  }

  if (phone) {
    const takenPhone = await getCustomerByPhone(phone);
    if (takenPhone && takenPhone.id !== customer.id) {
      return NextResponse.json(
        { error: "That mobile already has an account." },
        { status: 409 },
      );
    }
  }

  await updateCustomer(customer.id, {
    name,
    businessName: businessName || null,
    email,
    phone: phone || (body.phone?.trim() ? null : customer.phone),
  });

  const updated = await getCustomerById(customer.id);
  if (updated) {
    await createSession({
      customerId: updated.id,
      email: updated.email,
      role: "client",
    });
  }
  return NextResponse.json({
    ok: true,
    customer: updated
      ? {
          name: updated.name,
          businessName: updated.businessName,
          email: isPendingEmail(updated.email) ? "" : updated.email,
          phone: updated.phone,
        }
      : null,
  });
}
