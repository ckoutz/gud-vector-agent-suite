import { NextResponse } from "next/server";

import { requireAdmin } from "@/lib/api-auth";
import { hashPassword } from "@/lib/auth";
import { randomPassword } from "@/lib/ids";
import { sendEmail } from "@/lib/mail";
import { site } from "@/lib/site";
import { createCustomer, getCustomerByEmail } from "@/lib/store";

export async function POST(request: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  let body: { email?: string; name?: string };
  try {
    body = (await request.json()) as { email?: string; name?: string };
  } catch {
    return NextResponse.json({ error: "Invalid data." }, { status: 400 });
  }

  const email = body.email?.trim().toLowerCase() ?? "";
  const name = body.name?.trim() ?? "";
  if (!email || !name) {
    return NextResponse.json(
      { error: "Email and name are required." },
      { status: 400 },
    );
  }

  if (await getCustomerByEmail(email)) {
    return NextResponse.json(
      { error: "A customer with that email already exists." },
      { status: 409 },
    );
  }

  const password = randomPassword();
  const customer = await createCustomer({
    email,
    name,
    passwordHash: await hashPassword(password),
  });

  const mailed = await sendEmail({
    to: email,
    subject: "Your Güd Vector portal login",
    text: [
      `A customer portal account is ready at ${site.url}/portal/login`,
      `Email: ${email}`,
      `Temporary password: ${password}`,
      "Log in to review quotes, pay, or manage billing.",
    ].join("\n"),
  });

  return NextResponse.json({
    ok: true,
    customerId: customer.id,
    password: mailed.ok ? undefined : password,
    emailed: mailed.ok,
  });
}
