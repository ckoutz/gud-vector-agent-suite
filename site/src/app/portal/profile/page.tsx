import { redirect } from "next/navigation";

import { ProfileForm } from "@/components/portal/profile-form";
import { getSession } from "@/lib/auth";
import { isPendingEmail } from "@/lib/phone";
import { getCustomerById } from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Profile | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function ProfilePage() {
  const session = await getSession();
  if (!session) redirect("/portal/login");
  if (session.role === "admin") redirect("/portal");

  const customer = await getCustomerById(session.customerId);
  if (!customer) redirect("/portal/login");

  return (
    <div className="mx-auto w-full max-w-[640px] px-5 py-10 desktop:px-8">
      <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
        Customer
      </p>
      <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
        Profile
      </h1>
      <p className="mt-2 max-w-[40rem] text-sm text-neutral-600">
        Your name, business, email, and mobile for quotes and the portal.
      </p>
      <div className="mt-8">
        <ProfileForm
          name={customer.name}
          businessName={customer.businessName ?? ""}
          email={isPendingEmail(customer.email) ? "" : customer.email}
          phone={customer.phone ?? ""}
        />
      </div>
    </div>
  );
}
