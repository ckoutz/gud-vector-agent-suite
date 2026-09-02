import { createSession, hashPassword } from "@/lib/auth";
import { randomPassword } from "@/lib/ids";
import { isPendingEmail, samePhone, toE164 } from "@/lib/phone";
import {
  claimQuoteForCustomer,
  createCustomer,
  getCustomerByEmail,
  getCustomerById,
  getCustomerByPhone,
  getQuoteByToken,
  isPendingCustomer,
  updateCustomer,
  type Customer,
} from "@/lib/store";

export type SignupInput = {
  email?: string | null;
  phone?: string | null;
  name?: string | null;
  businessName?: string | null;
  password?: string | null;
  quoteToken?: string | null;
  google?: boolean;
  phoneVerified?: boolean;
};

export type SignupResult =
  | { ok: true; customer: Customer }
  | { ok: false; error: string; status: number };

async function assignedPendingFromQuote(token: string | null | undefined) {
  if (!token) return null;
  const quote = await getQuoteByToken(token);
  if (!quote?.customerId) return null;
  const assigned = await getCustomerById(quote.customerId);
  if (!assigned || assigned.role === "admin") return null;
  if (!isPendingCustomer(assigned)) return null;
  return assigned;
}

export function hasUsableEndUserIdentity(customer: Customer) {
  const name = customer.name.trim();
  if (!name || name.toLowerCase() === "customer") return false;
  if (!customer.email || isPendingEmail(customer.email)) return false;
  return true;
}

export async function sessionForEndUser(customer: Customer) {
  await createSession({
    customerId: customer.id,
    email: customer.email,
    role: customer.role === "admin" ? "admin" : "client",
  });
}

export async function completeEndUserSignup(
  input: SignupInput,
): Promise<SignupResult> {
  const email = input.email?.trim().toLowerCase() || null;
  const phone = toE164(input.phone);
  const name = input.name?.trim() || "";
  const businessName = input.businessName?.trim() || "";
  const quoteToken = input.quoteToken?.trim() || null;

  if (email) {
    const existing = await getCustomerByEmail(email);
    if (existing?.role === "admin") {
      return {
        ok: false,
        error: "Use the shop login for that email.",
        status: 403,
      };
    }
    if (existing && !isPendingCustomer(existing)) {
      if (input.google) {
        if (quoteToken) await claimQuoteForCustomer(quoteToken, existing);
        await sessionForEndUser(existing);
        return { ok: true, customer: existing };
      }
      return {
        ok: false,
        error: "That email already has an account. Log in instead.",
        status: 409,
      };
    }
  }

  let customer =
    (await assignedPendingFromQuote(quoteToken)) ||
    (email ? await getCustomerByEmail(email) : null) ||
    (phone ? await getCustomerByPhone(phone) : null);

  if (customer?.role === "admin") {
    return {
      ok: false,
      error: "Use the shop login for that account.",
      status: 403,
    };
  }

  if (customer && !isPendingCustomer(customer) && !input.google) {
    const phoneMatch =
      Boolean(input.phoneVerified && phone && samePhone(customer.phone, phone));
    if (!(phoneMatch && !hasUsableEndUserIdentity(customer))) {
      return {
        ok: false,
        error: "That account already exists. Log in instead.",
        status: 409,
      };
    }
  }

  if (customer && !isPendingCustomer(customer) && input.google) {
    if (quoteToken) await claimQuoteForCustomer(quoteToken, customer);
    await sessionForEndUser(customer);
    return { ok: true, customer };
  }

  const passwordHash = input.password
    ? await hashPassword(input.password)
    : await hashPassword(randomPassword());

  if (customer) {
    const patch: {
      name?: string;
      businessName?: string | null;
      phone?: string | null;
      email?: string;
      passwordHash?: string;
    } = { passwordHash };
    if (name) patch.name = name;
    if (businessName) patch.businessName = businessName;
    if (phone) patch.phone = phone;
    if (
      email &&
      (isPendingEmail(customer.email) ||
        isPendingCustomer(customer) ||
        input.phoneVerified)
    ) {
      patch.email = email;
    }
    await updateCustomer(customer.id, patch);
    customer = (await getCustomerById(customer.id)) ?? customer;
  } else {
    if (!email && !phone) {
      return {
        ok: false,
        error: "Email or phone is required.",
        status: 400,
      };
    }
    customer = await createCustomer({
      email:
        email ||
        `sms.${(phone || "0000000000").replace(/\D/g, "")}@pending.invalid`,
      name: name || "Customer",
      businessName: businessName || null,
      passwordHash,
      phone,
      role: "client",
    });
  }

  if (quoteToken) {
    await claimQuoteForCustomer(quoteToken, customer);
  }

  await sessionForEndUser(customer);
  return { ok: true, customer };
}
