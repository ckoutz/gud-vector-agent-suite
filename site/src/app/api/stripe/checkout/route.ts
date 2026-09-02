import { NextResponse } from "next/server";

export async function POST() {
  return NextResponse.json(
    {
      error: "Pay on the portal. Card details stay on this site.",
    },
    { status: 410 },
  );
}
