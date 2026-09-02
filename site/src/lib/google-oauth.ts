import { SignJWT, jwtVerify } from "jose";
import { NextResponse } from "next/server";

import { attachSessionCookie } from "@/lib/auth";
import { completeEndUserSignup } from "@/lib/end-user-auth";
import {
  googleMissingConfigMessage,
  googleOAuthReady,
} from "@/lib/provider-flags";
import { appUrl } from "@/lib/urls";

function secretKey() {
  const secret =
    process.env.AUTH_SECRET ||
    process.env.SESSION_SECRET ||
    (process.env.NODE_ENV !== "production" ? "gudvector-dev-secret" : "");
  if (!secret) return null;
  return new TextEncoder().encode(secret);
}

export function googleClientId() {
  return (
    process.env.AUTH_GOOGLE_ID?.trim() ||
    process.env.GOOGLE_CLIENT_ID?.trim() ||
    ""
  );
}

export function googleClientSecret() {
  return (
    process.env.AUTH_GOOGLE_SECRET?.trim() ||
    process.env.GOOGLE_CLIENT_SECRET?.trim() ||
    ""
  );
}

export { googleOAuthReady, googleMissingConfigMessage };

function requestOrigin(request: Request) {
  const url = new URL(request.url);
  if (
    url.hostname === "localhost" ||
    url.hostname === "127.0.0.1" ||
    url.hostname === "0.0.0.0"
  ) {
    const host = url.hostname === "0.0.0.0" ? "127.0.0.1" : url.hostname;
    return `${url.protocol}//${host}${url.port ? `:${url.port}` : ""}`;
  }
  const authUrl = process.env.AUTH_URL?.trim() || process.env.APP_URL?.trim();
  if (authUrl) return authUrl.replace(/\/$/, "");
  return appUrl();
}

export function googleCallbackPath(request: Request) {
  const path = new URL(request.url).pathname.replace(/\/$/, "");
  if (path.endsWith("/api/auth/google/callback")) {
    return "/api/auth/google/callback";
  }
  return "/api/auth/callback/google";
}

export function googleRedirectUri(request: Request) {
  const path = new URL(request.url).pathname.replace(/\/$/, "");
  const callbackPath =
    path === "/api/auth/google" || path === "/api/auth/signin/google"
      ? "/api/auth/callback/google"
      : googleCallbackPath(request);
  return `${requestOrigin(request)}${callbackPath}`;
}

export const GOOGLE_CALLBACK_URIS = [
  "https://gudvector.com/api/auth/callback/google",
  "http://127.0.0.1:43121/api/auth/callback/google",
  "http://localhost:43121/api/auth/callback/google",
] as const;

export async function googleStateToken(
  quote: string | null,
  next: "login" | "signup" = "signup",
) {
  const key = secretKey();
  if (!key) throw new Error("AUTH_SECRET is not set.");
  return new SignJWT({
    quote: quote || "",
    next,
    kind: "google_oauth",
  })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("15m")
    .sign(key);
}

export async function readGoogleState(token: string) {
  const key = secretKey();
  if (!key) return null;
  try {
    const { payload } = await jwtVerify(token, key);
    if (payload.kind !== "google_oauth") return null;
    return {
      quote: typeof payload.quote === "string" ? payload.quote : "",
      next: payload.next === "login" ? ("login" as const) : ("signup" as const),
    };
  } catch {
    return null;
  }
}

function errorPage(
  request: Request,
  next: "login" | "signup",
  message: string,
  quote?: string,
) {
  const dest = new URL(
    next === "login" ? "/portal/login" : "/portal/signup",
    requestOrigin(request),
  );
  dest.searchParams.set("error", message);
  if (quote) dest.searchParams.set("quote", quote);
  return NextResponse.redirect(dest);
}

export async function handleGoogleStart(request: Request) {
  const url = new URL(request.url);
  const quote = url.searchParams.get("quote");
  const next = url.searchParams.get("next") === "login" ? "login" : "signup";
  if (!googleOAuthReady()) {
    return errorPage(request, next, googleMissingConfigMessage(), quote || undefined);
  }
  try {
    const state = await googleStateToken(quote, next);
    const dest = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    dest.searchParams.set("client_id", googleClientId());
    dest.searchParams.set("redirect_uri", googleRedirectUri(request));
    dest.searchParams.set("response_type", "code");
    dest.searchParams.set("scope", "openid email profile");
    dest.searchParams.set("state", state);
    dest.searchParams.set("prompt", "select_account");
    return NextResponse.redirect(dest);
  } catch {
    return errorPage(
      request,
      next,
      "Google sign-in could not start. AUTH_SECRET may be missing.",
      quote || undefined,
    );
  }
}

export async function handleGoogleCallback(request: Request) {
  const url = new URL(request.url);
  const nextGuess =
    url.searchParams.get("next") === "login" ? "login" : "signup";
  if (!googleOAuthReady()) {
    return errorPage(request, nextGuess, googleMissingConfigMessage());
  }

  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  if (!code || !state) {
    return errorPage(
      request,
      nextGuess,
      "Google sign-in did not finish.",
    );
  }

  const parsed = await readGoogleState(state);
  if (!parsed) {
    return errorPage(
      request,
      nextGuess,
      "Google sign-in expired. Try again.",
    );
  }

  try {
    const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: googleClientId(),
        client_secret: googleClientSecret(),
        redirect_uri: googleRedirectUri(request),
        grant_type: "authorization_code",
      }),
    });
    const tokenJson = (await tokenRes.json()) as {
      access_token?: string;
      error?: string;
    };
    if (!tokenJson.access_token) {
      return errorPage(
        request,
        parsed.next,
        "Google sign-in did not finish.",
        parsed.quote,
      );
    }
    const userRes = await fetch(
      "https://openidconnect.googleapis.com/v1/userinfo",
      { headers: { Authorization: `Bearer ${tokenJson.access_token}` } },
    );
    const profile = (await userRes.json()) as {
      email?: string;
      name?: string;
    };
    if (!profile.email) {
      return errorPage(
        request,
        parsed.next,
        "Google did not share an email.",
        parsed.quote,
      );
    }

    const result = await completeEndUserSignup({
      email: profile.email,
      name: profile.name,
      quoteToken: parsed.quote || null,
      google: true,
    });
    if (!result.ok) {
      return errorPage(request, parsed.next, result.error, parsed.quote);
    }
    const response = NextResponse.redirect(new URL("/portal", requestOrigin(request)));
    await attachSessionCookie(response);
    return response;
  } catch {
    return errorPage(
      request,
      parsed.next,
      "Google sign-in did not finish.",
      parsed.quote,
    );
  }
}
