import { NextResponse } from "next/server";

import { getSession, isAdmin } from "@/lib/auth";

export async function requireCustomer() {
  const session = await getSession();
  if (!session) {
    return {
      session: null,
      error: NextResponse.json({ error: "Please log in." }, { status: 401 }),
    };
  }
  return { session, error: null };
}

export async function requireAdmin() {
  if (!(await isAdmin())) {
    return NextResponse.json({ error: "Admin login required." }, { status: 401 });
  }
  return null;
}

export async function requireShop() {
  const { session, error } = await requireCustomer();
  if (error || !session) return { session: null, error };
  if (session.role !== "admin") {
    return {
      session: null,
      error: NextResponse.json({ error: "Shop login required." }, { status: 403 }),
    };
  }
  return { session, error: null };
}
