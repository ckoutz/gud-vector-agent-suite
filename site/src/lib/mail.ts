import { Resend } from "resend";

import { site } from "@/lib/site";

export function canSendEmail() {
  return Boolean(process.env.RESEND_API_KEY);
}

export async function sendEmail({
  to,
  subject,
  text,
  replyTo,
}: {
  to: string;
  subject: string;
  text: string;
  replyTo?: string;
}): Promise<{ ok: true } | { ok: false; error: string }> {
  const key = process.env.RESEND_API_KEY;
  if (!key) {
    return { ok: false, error: "Email sending is not configured." };
  }

  const resend = new Resend(key);
  const from =
    process.env.RESEND_FROM ??
    `Güd Vector <beth.t@example.com>`;

  const { error } = await resend.emails.send({
    from,
    to,
    subject,
    text,
    replyTo,
  });

  if (error) {
    return { ok: false, error: error.message };
  }

  return { ok: true };
}

export async function sendQuoteInvite({
  to,
  acceptUrl,
  loginUrl,
  service,
}: {
  to: string;
  acceptUrl: string;
  loginUrl: string;
  service: string;
}) {
  return sendEmail({
    to,
    subject: "Your quote is ready",
    text: [
      `A quote for ${service} is ready.`,
      `Create an account to view it and accept or reject: ${acceptUrl}`,
      `Already have an account? ${loginUrl}`,
    ].join("\n"),
  });
}

export async function sendServiceChangeNotice({
  customerName,
  customerEmail,
  service,
  action,
}: {
  customerName: string;
  customerEmail: string;
  service: string;
  action: "paused" | "canceled";
}) {
  return sendEmail({
    to: site.email,
    subject: `Service ${action}`,
    text: [
      `${customerName} (${customerEmail}) ${action} ${service}.`,
      "Current customers in the portal will show the new status.",
    ].join("\n"),
    replyTo: customerEmail,
  });
}

export async function sendContactMessage({
  name,
  email,
  businessName,
  need,
  message,
}: {
  name: string;
  email: string;
  businessName: string;
  need: string;
  message: string;
}) {
  const text = [
    `Name: ${name}`,
    `Email: ${email}`,
    businessName ? `Business: ${businessName}` : "Business: (not given)",
    `Need: ${need}`,
    "",
    message,
  ].join("\n");

  return sendEmail({
    to: site.email,
    subject: `Güd Vector inquiry — ${need}`,
    text,
    replyTo: email,
  });
}
