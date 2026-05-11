"use client";

import { useState, useCallback } from "react";
import Chat from "@/components/Chat";
import FilePanel from "@/components/FilePanel";

export default function Home() {
  const [sessionKey, setSessionKey]   = useState(0);
  const [threadId, setThreadId]       = useState("");
  const [fileVersion, setFileVersion] = useState(0);

  const handleFilesChanged = useCallback(() => {
    setFileVersion((v) => v + 1);
  }, []);

  const handleNewSession = useCallback(() => {
    setThreadId("");
    setFileVersion(0);
    setSessionKey((k) => k + 1); // remounts Chat + FilePanel, clearing all state
  }, []);

  return (
    <div className="flex h-screen bg-neutral-950 text-white">
      {/* Main chat area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <header className="flex items-center gap-3 border-b border-neutral-800 px-4 py-3 shrink-0">
          <div className="h-2 w-2 rounded-full bg-emerald-400" />
          <span className="text-sm font-semibold">Deep Agent</span>
          {threadId && (
            <span className="ml-2 font-mono text-xs text-neutral-600 truncate max-w-[200px]">
              {threadId}
            </span>
          )}
          <button
            onClick={handleNewSession}
            className="ml-auto rounded-lg border border-neutral-700 px-3 py-1 text-xs text-neutral-400 hover:border-neutral-500 hover:text-white transition-colors"
          >
            New Session
          </button>
        </header>

        <Chat
          key={sessionKey}
          threadId={threadId}
          onThreadId={setThreadId}
          onFilesChanged={handleFilesChanged}
        />
      </div>

      {/* File panel */}
      <FilePanel key={`${sessionKey}-${fileVersion}`} threadId={threadId} />
    </div>
  );
}
