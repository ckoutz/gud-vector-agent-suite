import { jwtVerify } from "jose";
import { NextResponse, type NextRequest } from "next/server";

function redirectToLogin(request: NextRequest) {
  const dest = new URL("/portal/login", request.url);
  const quote = request.nextUrl.searchParams.get("quote");
  if (quote) dest.searchParams.set("quote", quote);
  return NextResponse.redirect(dest);
}

function secretKey() {
  const secret =
    process.env.AUTH_SECRET ||
    process.env.SESSION_SECRET ||
    (process.env.NODE_ENV !== "production" ? "gudvector-dev-secret" : "");
  if (!secret) return null;
  return new TextEncoder().encode(secret);
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (
    pathname === "/portal/login" ||
    pathname === "/portal/signup" ||
    pathname.startsWith("/portal/admin")
  ) {
    return NextResponse.next();
  }

  const token = request.cookies.get("gv_session")?.value;
  const key = secretKey();
  if (!token || !key) {
    return redirectToLogin(request);
  }

  try {
    await jwtVerify(token, key);
    return NextResponse.next();
  } catch {
    return redirectToLogin(request);
  }
}

export const config = {
  matcher: ["/portal", "/portal/:path*"],
};
