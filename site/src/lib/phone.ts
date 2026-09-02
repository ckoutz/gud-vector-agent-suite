export function phoneDigits(value: string | null | undefined) {
  const digits = (value ?? "").replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) return digits.slice(1);
  return digits;
}

export function toE164(value: string | null | undefined) {
  const digits = phoneDigits(value);
  if (digits.length !== 10) return null;
  return `+1${digits}`;
}

export function samePhone(
  left: string | null | undefined,
  right: string | null | undefined,
) {
  const a = phoneDigits(left);
  const b = phoneDigits(right);
  return a.length === 10 && a === b;
}

export function looksLikeEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

export function pendingEmailForPhone(phone: string) {
  return `sms.${phoneDigits(phone)}@pending.invalid`;
}

export function isPendingEmail(email: string | null | undefined) {
  return (email ?? "").toLowerCase().endsWith("@pending.invalid");
}
