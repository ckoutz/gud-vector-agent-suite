import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function StatusChip({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "text-bubble inline-flex rounded-full px-3 py-1 text-sm font-semibold tracking-[0.14em] uppercase",
        className,
      )}
    >
      {children}
    </p>
  );
}
