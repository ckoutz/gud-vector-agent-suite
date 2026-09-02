export function twilioAccountReady() {
  return Boolean(
    process.env.TWILIO_ACCOUNT_SID?.trim() &&
      process.env.TWILIO_AUTH_TOKEN?.trim(),
  );
}

export function twilioVerifyServiceSid() {
  return (
    process.env.TWILIO_VERIFY_SERVICE_SID?.trim() ||
    process.env.VERIFY_SERVICE_SID?.trim() ||
    ""
  );
}

export function twilioVerifyReady() {
  return twilioAccountReady() && Boolean(twilioVerifyServiceSid());
}

export function googleOAuthReady() {
  return Boolean(
    (process.env.AUTH_GOOGLE_ID?.trim() ||
      process.env.GOOGLE_CLIENT_ID?.trim()) &&
      (process.env.AUTH_GOOGLE_SECRET?.trim() ||
        process.env.GOOGLE_CLIENT_SECRET?.trim()),
  );
}

export function googleMissingConfigMessage() {
  return "Google sign-in is not configured. Set AUTH_GOOGLE_ID and AUTH_GOOGLE_SECRET (or GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) in Vercel.";
}

export function providerSignInLead(
  googleReady: boolean,
  phoneReady: boolean,
) {
  if (googleReady && phoneReady) {
    return "Use email, a mobile number, or Google.";
  }
  if (googleReady) return "Use email or Google.";
  if (phoneReady) return "Use email or a mobile number.";
  return "Use email.";
}
