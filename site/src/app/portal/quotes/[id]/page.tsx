import { notFound, redirect } from "next/navigation";

import { LiveQuoteCard } from "@/components/portal/live-quote-card";
import { canAccessQuote, getSession } from "@/lib/auth";
import { getQuoteById } from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Quote | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function QuoteDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role !== "admin") redirect("/portal");

  const { id } = await params;
  const quote = await getQuoteById(id);
  if (!quote || !canAccessQuote(session, quote)) notFound();

  return (
    <div className="mx-auto w-full max-w-[760px] px-5 py-10 desktop:px-8">
      <ul className="grid gap-4">
        <LiveQuoteCard quote={quote} />
      </ul>
    </div>
  );
}
