export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    const { ensureIncomingSmsWebhook } = await import("./lib/twilio-webhook");
    await ensureIncomingSmsWebhook();
  } catch {
    // Marketing pages must still boot if Twilio is unset or Verify is missing.
  }
}
