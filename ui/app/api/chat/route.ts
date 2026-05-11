import { NextRequest } from "next/server";

const AGENT = process.env.AGENT_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { thread_id } = body;

  const res = await fetch(`${AGENT}/threads/${thread_id}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok || !res.body) {
    return new Response(
      `data: ${JSON.stringify({ type: "error", message: `Agent error ${res.status}` })}\n\n`,
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  // Pass the SSE stream straight through to the browser
  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "X-Accel-Buffering": "no",
    },
  });
}
