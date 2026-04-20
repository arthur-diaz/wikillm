import { useEffect, useRef, useState } from "react";
import { Send, Lightbulb, Bookmark } from "lucide-react";
import ReactMarkdown from "react-markdown";

type Msg = { id: number; role: "user" | "assistant" | "suggest"; content: string; loading: boolean };

let _nextId = 0;

export default function ChatPanel() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [fileBack, setFileBack] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const push = (m: Omit<Msg, "id">) => {
    const id = _nextId++;
    setMessages(prev => [...prev, { ...m, id }]);
    return id;
  };

  const patch = (id: number, diff: Partial<Msg>) =>
    setMessages(prev => prev.map(m => m.id === id ? { ...m, ...diff } : m));

  const streamSSE = (url: string, role: Msg["role"]) => {
    esRef.current?.close();
    setBusy(true);
    const msgId = push({ role, content: "", loading: true });
    const es = new EventSource(url);
    esRef.current = es;
    let buf = "";
    es.addEventListener("token", e => {
      buf += (e as MessageEvent).data;
      patch(msgId, { content: buf });
    });
    es.addEventListener("error", e => {
      const d = (e as MessageEvent).data;
      if (d) patch(msgId, { content: d, loading: false });
      else patch(msgId, { loading: false });
      setBusy(false); es.close();
    });
    es.addEventListener("done", () => {
      patch(msgId, { loading: false });
      setBusy(false); es.close();
    });
    es.onerror = () => { patch(msgId, { loading: false }); setBusy(false); es.close(); };
  };

  const send = () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    push({ role: "user", content: q, loading: false });
    streamSSE(`/api/query?question=${encodeURIComponent(q)}&file_back=${fileBack}`, "assistant");
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const analyse = () => {
    if (busy) return;
    streamSSE("/api/suggest", "suggest");
  };

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3">
            <MessageIcon />
            <p className="text-sm text-gray-600">Pose une question sur le wiki, ou analyse son contenu.</p>
          </div>
        )}
        {messages.map(msg => (
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            {msg.role === "user" ? (
              <div className="max-w-xl bg-blue-600 text-white text-sm rounded-2xl rounded-br-sm px-4 py-2.5">
                {msg.content}
              </div>
            ) : (
              <div className={`w-full max-w-3xl rounded-xl px-4 py-3 border ${
                msg.role === "suggest"
                  ? "border-yellow-800/50 bg-yellow-950/10"
                  : "border-gray-800"
              }`} style={{ background: msg.role === "suggest" ? undefined : "#161b22" }}>
                {msg.role === "suggest" && (
                  <p className="flex items-center gap-1.5 text-xs text-yellow-500/80 mb-2">
                    <Lightbulb size={11} /> Analyse du wiki
                  </p>
                )}
                <div className="prose prose-invert prose-sm max-w-none">
                  <ReactMarkdown>{msg.content || (msg.loading ? "\u00a0" : "")}</ReactMarkdown>
                </div>
                {msg.loading && <span className="inline-block w-1.5 h-3.5 bg-current opacity-60 animate-pulse ml-0.5 align-middle" />}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="border-t border-gray-800 p-3" style={{ background: "#161b22" }}>
        <div className="flex items-end gap-2">
          <button
            onClick={analyse}
            disabled={busy}
            title="Analyser le wiki"
            className="flex items-center gap-1.5 text-xs text-yellow-400 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 px-3 py-2 rounded-lg flex-shrink-0 whitespace-nowrap"
          >
            <Lightbulb size={13} />
            <span className="hidden sm:inline">Analyser</span>
          </button>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = e.target.scrollHeight + "px"; }}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Pose une question… (Entrée pour envoyer)"
            disabled={busy}
            rows={1}
            className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-500 resize-none disabled:opacity-40"
            style={{ minHeight: "38px", maxHeight: "120px", overflowY: "auto" }}
          />

          <label className="flex items-center gap-1 text-gray-600 hover:text-gray-400 cursor-pointer flex-shrink-0 py-2" title="Sauvegarder la réponse en page wiki">
            <input type="checkbox" checked={fileBack} onChange={e => setFileBack(e.target.checked)} className="accent-blue-500 w-3 h-3" />
            <Bookmark size={13} className={fileBack ? "text-blue-400" : ""} />
          </label>

          <button
            onClick={send}
            disabled={busy || !input.trim()}
            className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white p-2 rounded-lg flex-shrink-0"
          >
            <Send size={15} />
          </button>
        </div>
        {fileBack && (
          <p className="text-xs text-blue-400/70 mt-1.5 ml-1">Les réponses seront sauvegardées en page wiki.</p>
        )}
      </div>
    </div>
  );
}

function MessageIcon() {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-gray-800">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}
