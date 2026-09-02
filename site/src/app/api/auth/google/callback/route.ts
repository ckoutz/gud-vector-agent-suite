import { handleGoogleCallback } from "@/lib/google-oauth";

export async function GET(request: Request) {
  return handleGoogleCallback(request);
}
