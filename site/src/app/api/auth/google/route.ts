import { handleGoogleStart } from "@/lib/google-oauth";

export async function GET(request: Request) {
  return handleGoogleStart(request);
}
