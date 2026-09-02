import { AdminLoginForm } from "@/components/portal/admin-login-form";
import { AdminTools } from "@/components/portal/admin-tools";
import { PortalHeader } from "@/components/portal/portal-header";
import { isAdmin } from "@/lib/auth";
import { listCustomers, listRecentQuotes } from "@/lib/store";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Admin | Güd Vector",
  robots: { index: false, follow: false },
};

export default async function AdminPage() {
  const allowed = await isAdmin();

  return (
    <>
      <PortalHeader />
      <div className="mx-auto w-full max-w-[1100px] px-5 py-10 desktop:px-8">
        {allowed ? (
          <>
            <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-brand uppercase">
              Admin
            </p>
            <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-charcoal">
              Customers and quotes
            </h1>
            <p className="mt-2 max-w-[36rem] text-sm text-neutral-600">
              Create a customer, then generate a quote. Customers log in at
              /portal/login.
            </p>
            <div className="mt-8">
              <AdminTools
                customers={await listCustomers()}
                quotes={await listRecentQuotes()}
              />
            </div>
          </>
        ) : (
          <AdminLoginForm />
        )}
      </div>
    </>
  );
}
