import { NextResponse } from "next/server";

const AGENT = process.env.AGENT_URL ?? "http://localhost:8000";

export async function POST() {
  const res = await fetch(`${AGENT}/threads`, { method: "POST" });
  const data = await res.json();
  return NextResponse.json(data, { status: res.ok ? 201 : 500 });
}
