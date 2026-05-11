import { NextRequest, NextResponse } from "next/server";

const AGENT = process.env.AGENT_URL ?? "http://localhost:8000";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string }> },
) {
  const { path } = await params;
  const thread = req.nextUrl.searchParams.get("thread") ?? "";

  const res = await fetch(`${AGENT}/threads/${thread}/content/${path}`);
  if (!res.ok) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const filename = path.split("/").pop() ?? path;
  return new NextResponse(res.body, {
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/octet-stream",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}
