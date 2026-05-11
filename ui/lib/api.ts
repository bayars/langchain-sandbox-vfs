export type SSEEvent =
  | { type: "token";      content: string }
  | { type: "tool_start"; name: string; input: Record<string, unknown> }
  | { type: "tool_end";   name: string; output: string }
  | { type: "done";       thread_id: string }
  | { type: "error";      message: string };

export async function createThread(): Promise<string> {
  const res = await fetch("/api/threads", { method: "POST" });
  if (!res.ok) throw new Error("Failed to create thread");
  const data = await res.json();
  return data.thread_id;
}

export async function* streamRun(
  threadId: string,
  message: string,
): AsyncGenerator<SSEEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
  });

  if (!res.ok || !res.body) throw new Error(`Agent error: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        yield JSON.parse(raw) as SSEEvent;
      } catch {
        // ignore malformed lines
      }
    }
  }
}

export async function listFiles(threadId: string): Promise<string[]> {
  const res = await fetch(`/api/files?thread=${threadId}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export async function uploadFile(threadId: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/files?thread=${threadId}`, { method: "POST", body: form });
  if (!res.ok) throw new Error("Upload failed");
}

export async function downloadFile(threadId: string, path: string): Promise<void> {
  const url = `/api/files/${encodeURIComponent(path)}?thread=${threadId}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = path.split("/").pop() ?? path;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}
