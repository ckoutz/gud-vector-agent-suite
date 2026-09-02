"use client";

export function LogoutButton() {
  return (
    <button
      type="button"
      className="text-sm font-medium text-charcoal hover:text-brand"
      onClick={async () => {
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.href = "/portal/login";
      }}
    >
      Log out
    </button>
  );
}
