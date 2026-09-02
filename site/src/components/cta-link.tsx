import { type ReactNode } from "react";
import { type VariantProps } from "class-variance-authority";
import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ButtonVariantProps = VariantProps<typeof buttonVariants>;

export function CtaLink({
  href,
  children,
  className,
  variant = "default",
  size = "lg",
}: {
  href: string;
  children: ReactNode;
  className?: string;
} & ButtonVariantProps) {
  const classes = cn(
    buttonVariants({ variant, size }),
    "h-11 rounded-full px-5 text-[0.95rem]",
    variant === "default" &&
      "bg-brand text-brand-foreground hover:bg-[#e56504]",
    variant === "outline" &&
      "border-neutral-300 bg-[#FDFDFD] text-charcoal hover:bg-neutral-50",
    className,
  );

  if (href.startsWith("mailto:") || href.startsWith("http")) {
    return (
      <a href={href} className={classes}>
        {children}
      </a>
    );
  }

  return (
    <Link href={href} className={classes}>
      {children}
    </Link>
  );
}
