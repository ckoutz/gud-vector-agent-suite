import { PortalNav } from "@/components/portal/portal-nav";
import type { PortalRole } from "@/lib/store";

export function PortalShell({
  role,
  children,
}: {
  role: PortalRole;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-full flex-col bg-[#FDFDFD] desktop:flex-row">
      <PortalNav role={role} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
