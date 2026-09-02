import { timingSafeEqual } from "node:crypto";

import bcrypt from "bcryptjs";
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

import {
  createCustomer,
  getCustomerByEmail,
  getCustomerById,
  removeShopDeskSeedQuotes,
  seedGlenQuote,
  updateCustomer,
  type PortalRole,
} from "@/lib/store";

const SESSION_COOKIE = "gv_session";
const ADMIN_COOKIE = "gv_admin";

export type Session = {
  customerId: string;
  email: string;
  role: PortalRole;
};

function secretKey() {
  const secret =
    process.env.AUTH_SECRET ||
    process.env.SESSION_SECRET ||
    (process.env.NODE_ENV !== "production" ? "gudvector-dev-secret" : "");
  if (!secret) return null;
  return new TextEncoder().encode(secret);
}

export function authConfigured() {
  return Boolean(secretKey());
}

export async function hashPassword(password: string) {
  return bcrypt.hash(password, 12);
}

export async function verifyPassword(password: string, hash: string) {
  if (!password || !hash || !hash.startsWith("$2")) return false;
  return bcrypt.compare(password, hash);
}

export function sessionCookieOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  };
}

export async function createSession(session: Session) {
  const key = secretKey();
  if (!key) throw new Error("AUTH_SECRET is not set.");
  const token = await new SignJWT(session)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("30d")
    .sign(key);
  const jar = await cookies();
  jar.set(SESSION_COOKIE, token, sessionCookieOptions());
  return token;
}

export async function attachSessionCookie(response: {
  cookies: { set: (name: string, value: string, options: ReturnType<typeof sessionCookieOptions>) => unknown };
}) {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (token) {
    response.cookies.set(SESSION_COOKIE, token, sessionCookieOptions());
  }
  return response;
}

export async function clearSession() {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
}

export async function getSession(): Promise<Session | null> {
  const key = secretKey();
  if (!key) return null;
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, key);
    if (
      typeof payload.customerId !== "string" ||
      typeof payload.email !== "string"
    ) {
      return null;
    }
    if (payload.role === "admin" || payload.role === "client") {
      return {
        customerId: payload.customerId,
        email: payload.email,
        role: payload.role,
      };
    }
    const customer = await getCustomerById(payload.customerId);
    return {
      customerId: payload.customerId,
      email: payload.email,
      role: customer?.role === "admin" ? "admin" : "client",
    };
  } catch {
    return null;
  }
}

function safeEqual(a: string, b: string) {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export async function createAdminSession() {
  const key = secretKey();
  if (!key) throw new Error("AUTH_SECRET is not set.");
  const token = await new SignJWT({ admin: true })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("12h")
    .sign(key);
  const jar = await cookies();
  jar.set(ADMIN_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 12,
  });
}

export async function clearAdminSession() {
  const jar = await cookies();
  jar.delete(ADMIN_COOKIE);
}

export async function isAdmin() {
  const key = secretKey();
  if (!key) return false;
  const jar = await cookies();
  const token = jar.get(ADMIN_COOKIE)?.value;
  if (!token) return false;
  try {
    const { payload } = await jwtVerify(token, key);
    return payload.admin === true;
  } catch {
    return false;
  }
}

export function checkAdminPassword(password: string) {
  const expected = process.env.ADMIN_PASSWORD;
  if (!expected) return false;
  return safeEqual(password, expected);
}

export function canAccessQuote(
  session: Session,
  quote: {
    customerId: string | null;
    customerEmail?: string | null;
    customerPhone?: string | null;
  },
  customer?: { email?: string | null; phone?: string | null } | null,
) {
  if (session.role === "admin") return true;
  if (quote.customerId === session.customerId) return true;
  if (quote.customerEmail && quote.customerEmail === session.email) return true;
  if (customer?.email && quote.customerEmail === customer.email) return true;
  if (customer?.phone && quote.customerPhone) {
    const a = quote.customerPhone.replace(/\D/g, "");
    const b = customer.phone.replace(/\D/g, "");
    const ten = (value: string) =>
      value.length === 11 && value.startsWith("1") ? value.slice(1) : value;
    if (ten(a).length === 10 && ten(a) === ten(b)) return true;
  }
  return false;
}

export async function bootstrapPortalUser() {
  const clientEmail = (
    process.env.PORTAL_BOOTSTRAP_EMAIL?.trim() || "glen@protech-cal.com"
  ).toLowerCase();
  const clientPassword = process.env.PORTAL_BOOTSTRAP_PASSWORD || "protech";

  let client = await getCustomerByEmail(clientEmail);
  if (!client) {
    client = await createCustomer({
      email: clientEmail,
      name: "ProTech",
      passwordHash: await hashPassword(clientPassword),
      role: "client",
    });
  } else {
    if (client.name === "Account") {
      await updateCustomer(client.id, { name: "ProTech" });
    }
    if (client.role !== "client") {
      await updateCustomer(client.id, { role: "client" });
    }
  }
  await removeShopDeskSeedQuotes(client.id);
  if (clientEmail === "glen@protech-cal.com") {
    await seedGlenQuote(client.id);
  }

  const adminEmail = (
    process.env.PORTAL_ADMIN_EMAIL?.trim() || "cameron@gudvector.com"
  ).toLowerCase();
  const adminPassword = process.env.PORTAL_ADMIN_PASSWORD || "c";
  let admin = await getCustomerByEmail(adminEmail);
  if (!admin) {
    await createCustomer({
      email: adminEmail,
      name: "Admin",
      passwordHash: await hashPassword(adminPassword),
      role: "admin",
    });
  } else if (admin.role !== "admin") {
    await updateCustomer(admin.id, { role: "admin", name: "Admin" });
  }
}
