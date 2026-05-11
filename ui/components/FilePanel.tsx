"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { downloadFile, listFiles, uploadFile } from "@/lib/api";

type Props = { threadId: string };

export default function FilePanel({ threadId }: Props) {
  const [files, setFiles]     = useState<string[]>([]);
  const [uploading, setUpload] = useState(false);
  const inputRef              = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    if (!threadId) return;
    setFiles(await listFiles(threadId));
  }, [threadId]);

  useEffect(() => { refresh(); }, [refresh]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUpload(true);
    try {
      await uploadFile(threadId, file);
      await refresh();
    } finally {
      setUpload(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function handleDownload(path: string) {
    await downloadFile(threadId, path);
  }

  return (
    <aside className="flex flex-col gap-3 h-full border-l border-neutral-800 p-4 w-64 shrink-0">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-neutral-400 uppercase tracking-wider">
          Files
        </span>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading || !threadId}
          className="rounded px-2 py-0.5 text-xs bg-neutral-800 hover:bg-neutral-700 text-neutral-300 disabled:opacity-40"
        >
          {uploading ? "…" : "Upload"}
        </button>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          onChange={handleUpload}
        />
      </div>

      <div className="flex flex-col gap-1 overflow-y-auto flex-1 min-h-0">
        {files.length === 0 && (
          <p className="text-xs text-neutral-600 italic">No files yet</p>
        )}
        {files.map((f) => (
          <button
            key={f}
            onClick={() => handleDownload(f)}
            className="text-left text-xs text-blue-400 hover:text-blue-300 truncate font-mono py-0.5"
            title={f}
          >
            {f}
          </button>
        ))}
      </div>

      <button
        onClick={refresh}
        className="text-xs text-neutral-600 hover:text-neutral-400"
      >
        Refresh
      </button>
    </aside>
  );
}
