export function verifySidFromEnv() {
  return (
    process.env.TWILIO_VERIFY_SERVICE_SID?.trim() ||
    process.env.VERIFY_SERVICE_SID?.trim() ||
    ""
  );
}

export function getVerifyServiceSid() {
  const sid = verifySidFromEnv();
  if (!sid) {
    throw new Error(
      "Phone signup is not configured. Set TWILIO_VERIFY_SERVICE_SID or VERIFY_SERVICE_SID.",
    );
  }
  return sid;
}
