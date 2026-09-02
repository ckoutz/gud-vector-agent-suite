import { getSession } from "@/lib/auth";
import { PortalShell } from "@/components/portal/portal-shell";

export default async function PortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();

  if (!session) {
    return (
      <div className="flex min-h-full flex-col bg-[#FDFDFD]">{children}</div>
    );
  }

  return <PortalShell role={session.role}>{children}</PortalShell>;
}
