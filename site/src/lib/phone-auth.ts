import { SignJWT, jwtVerify } from "jose";

function secretKey() {
  const secret =
    process.env.AUTH_SECRET ||
    process.env.SESSION_SECRET ||
    (process.env.NODE_ENV !== "production" ? "gudvector-dev-secret" : "");
  if (!secret) return null;
  return new TextEncoder().encode(secret);
}

export async function phoneVerifiedToken(
  phone: string,
  quote: string | null,
) {
  const key = secretKey();
  if (!key) throw new Error("AUTH_SECRET is not set.");
  return new SignJWT({
    phone,
    quote: quote || "",
    kind: "phone_verified",
  })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("15m")
    .sign(key);
}

export async function readPhoneVerifiedToken(token: string) {
  const key = secretKey();
  if (!key) return null;
  try {
    const { payload } = await jwtVerify(token, key);
    if (payload.kind !== "phone_verified") return null;
    if (typeof payload.phone !== "string" || !payload.phone) return null;
    return {
      phone: payload.phone,
      quote: typeof payload.quote === "string" ? payload.quote : "",
    };
  } catch {
    return null;
  }
}
