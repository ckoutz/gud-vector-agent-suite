import { site } from "@/lib/site";

export function appUrl() {
  return (process.env.APP_URL || site.url).replace(/\/$/, "");
}

export function quotePublicUrl(token: string) {
  return `${appUrl()}/q/${token}`;
}

export function quoteSignupUrl(token: string) {
  return `${appUrl()}/portal/signup?quote=${encodeURIComponent(token)}`;
}
