"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { createThread, SSEEvent, streamRun, uploadFile } from "@/lib/api";
import ToolCallBadge from "./ToolCallBadge";

type ToolCall = { name: string; status: "running" | "done"; output?: string };

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  attachments?: string[];
};

type Props = {
  threadId: string;
  onThreadId: (id: string) => void;
  onFilesChanged: () => void;
};

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

export default function Chat({ threadId, onThreadId, onFilesChanged }: Props) {
  const [messages, setMessages]     = useState<Message[]>([]);
  const [input, setInput]           = useState("");
  const [busy, setBusy]             = useState(false);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [uploading, setUploading]   = useState(false);
  const bottomRef                   = useRef<HTMLDivElement>(null);
  const fileInputRef                = useRef<HTMLInputElement>(null);

  const scroll = () => bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  useEffect(scroll, [messages]);

  const ensureThread = useCallback(async (): Promise<string> => {
    if (threadId) return threadId;
    const tid = await createThread();
    onThreadId(tid);
    return tid;
  }, [threadId, onThreadId]);

  const handleAttach = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (!files.length) return;
      setUploading(true);
      try {
        const tid = await ensureThread();
        await Promise.all(files.map((f) => uploadFile(tid, f)));
        setAttachments((prev) => [...prev, ...files]);
        onFilesChanged();
      } catch (err) {
        console.error("Upload failed:", err);
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [ensureThread, onFilesChanged],
  );

  const removeAttachment = (name: string) =>
    setAttachments((prev) => prev.filter((f) => f.name !== name));

  const send = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const text = input.trim();
      if ((!text && attachments.length === 0) || busy) return;

      const tid = await ensureThread();

      const attachNames = attachments.map((f) => f.name);
      const fullText =
        attachNames.length > 0
          ? `${text}\n\n[Attached files: ${attachNames.join(", ")}]`.trim()
          : text;

      const userMsg: Message = {
        id: uid(),
        role: "user",
        content: text || `(attached: ${attachNames.join(", ")})`,
        toolCalls: [],
        attachments: attachNames.length > 0 ? attachNames : undefined,
      };
      const assistantMsg: Message = {
        id: uid(),
        role: "assistant",
        content: "",
        toolCalls: [],
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setAttachments([]);
      setBusy(true);

      try {
        for await (const event of streamRun(tid, fullText)) {
          handleEvent(event, assistantMsg.id);
        }
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content + `\n\n[Error: ${err}]` }
              : m,
          ),
        );
      } finally {
        setBusy(false);
        onFilesChanged();
      }
    },
    [input, attachments, busy, ensureThread, onFilesChanged],
  );

  function handleEvent(event: SSEEvent, msgId: string) {
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== msgId) return m;
        if (event.type === "token") {
          return { ...m, content: m.content + event.content };
        }
        if (event.type === "tool_start") {
          return {
            ...m,
            toolCalls: [...m.toolCalls, { name: event.name, status: "running" }],
          };
        }
        if (event.type === "tool_end") {
          return {
            ...m,
            toolCalls: m.toolCalls.map((tc) =>
              tc.name === event.name && tc.status === "running"
                ? { ...tc, status: "done", output: event.output }
                : tc,
            ),
          };
        }
        return m;
      }),
    );
  }

  const canSend = (input.trim().length > 0 || attachments.length > 0) && !busy && !uploading;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <p className="text-neutral-600 text-sm text-center mt-20">
            Send a message to start.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] rounded-xl px-4 py-2.5 text-sm whitespace-pre-wrap ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-100"
              }`}
            >
              {msg.toolCalls.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1">
                  {msg.toolCalls.map((tc, i) => (
                    <ToolCallBadge
                      key={i}
                      name={tc.name}
                      status={tc.status}
                      output={tc.output}
                    />
                  ))}
                </div>
              )}
              {msg.attachments && msg.attachments.length > 0 && (
                <div className="mb-1 flex flex-wrap gap-1">
                  {msg.attachments.map((name) => (
                    <span
                      key={name}
                      className="inline-flex items-center gap-1 rounded bg-blue-700/50 px-1.5 py-0.5 text-xs font-mono"
                    >
                      📎 {name}
                    </span>
                  ))}
                </div>
              )}
              {msg.content || (
                <span className="text-neutral-500 animate-pulse">▍</span>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Attachment chips above input */}
      {attachments.length > 0 && (
        <div className="border-t border-neutral-800 px-3 pt-2 flex flex-wrap gap-1">
          {attachments.map((f) => (
            <span
              key={f.name}
              className="inline-flex items-center gap-1 rounded bg-neutral-700 px-2 py-0.5 text-xs font-mono text-neutral-200"
            >
              📎 {f.name}
              <button
                onClick={() => removeAttachment(f.name)}
                className="ml-0.5 text-neutral-400 hover:text-white"
                aria-label="Remove attachment"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input bar */}
      <form
        onSubmit={send}
        className="border-t border-neutral-800 p-3 flex gap-2 items-center"
      >
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleAttach}
        />

        {/* Attach button */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
          title="Attach files"
          className="shrink-0 rounded-lg p-2.5 text-neutral-400 hover:text-white hover:bg-neutral-700 disabled:opacity-40 transition-colors"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        </button>

        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) send(e as unknown as FormEvent);
            }
          }}
          disabled={busy}
          placeholder={uploading ? "Uploading…" : "Message the agent…"}
          className="flex-1 rounded-lg bg-neutral-800 px-4 py-2.5 text-sm text-white placeholder-neutral-500 outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!canSend}
          className="shrink-0 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40 transition-colors"
        >
          {busy ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
