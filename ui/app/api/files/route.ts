import { NextRequest, NextResponse } from "next/server";

const AGENT = process.env.AGENT_URL ?? "http://localhost:8000";

export async function GET(req: NextRequest) {
  const thread = req.nextUrl.searchParams.get("thread") ?? "";
  const res = await fetch(`${AGENT}/threads/${thread}/files`);
  const data = await res.json();
  return NextResponse.json(data);
}

export async function POST(req: NextRequest) {
  const thread = req.nextUrl.searchParams.get("thread") ?? "";
  const form = await req.formData();
  const res = await fetch(`${AGENT}/threads/${thread}/files`, {
    method: "POST",
    body: form,
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.ok ? 201 : 500 });
}
