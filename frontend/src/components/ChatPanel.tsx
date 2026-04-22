import { useEffect, useRef, useState } from "react";
import { ArrowUp, Lightbulb, Bookmark, Plus, ChevronDown } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { ACCENT } from "./ui/design";

type Msg = { id: number; role: "user" | "assistant" | "suggest"; content: string; loading: boolean };

let _nextId = 0;

const SUGGESTIONS = [
  "Résume les pages ajoutées cette semaine",
  "Liens entre « mémoire » et « attention »",
  "Cherche des contradictions",
];

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
    es.addEventListener("token", e => { buf += (e as MessageEvent).data; patch(msgId, { content: buf }); });
    es.addEventListener("error", e => {
      const d = (e as MessageEvent).data;
      if (d) patch(msgId, { content: d, loading: false });
      else patch(msgId, { loading: false });
      setBusy(false); es.close();
    });
    es.addEventListener("done", () => { patch(msgId, { loading: false }); setBusy(false); es.close(); });
    es.onerror = () => { patch(msgId, { loading: false }); setBusy(false); es.close(); };
  };

  const send = () => {
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
    push({ role: "user", content: q, loading: false });
    streamSSE(`/api/query?question=${encodeURIComponent(q)}&file_back=${fileBack}`, "assistant");
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const analyse = () => { if (busy) return; streamSSE("/api/suggest", "suggest"); };

  const isEmpty = messages.length === 0;

  /* Composer (reused in both states) */
  const Composer = (
    <div className="w-full max-w-[720px] mx-auto">
      <div className="rounded-2xl border border-[#212226] bg-[#17181b] focus-within:border-[#2a2b31] transition-colors">
        <div className="px-4 pt-3.5">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => {
              setInput(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
            }}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder="Pose une question sur ton wiki…"
            disabled={busy}
            rows={1}
            className="w-full bg-transparent outline-none text-[14.5px] leading-relaxed text-[#ececed] placeholder:text-[#6b6c72] resize-none disabled:opacity-50 ui-scroll"
            style={{ minHeight: 24, maxHeight: 200 }}
          />
        </div>
        <div className="flex items-center justify-between px-2.5 pb-2.5 pt-2">
          <div className="flex items-center gap-1">
            <button
              title="Ajouter"
              className="w-7 h-7 grid place-items-center rounded-md text-[#6b6c72] hover:text-[#ececed] hover:bg-[#1d1e22] transition-colors"
            >
              <Plus size={14} strokeWidth={1.8} />
            </button>
            <button
              onClick={analyse}
              disabled={busy}
              title="Analyser le wiki"
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[#a1a1a6] hover:text-[#ececed] hover:bg-[#1d1e22] text-[12px] font-medium transition-colors disabled:opacity-40"
            >
              <Lightbulb size={13} strokeWidth={1.8} />
              Analyser
            </button>
            <button
              onClick={() => setFileBack(v => !v)}
              title="Sauvegarder la réponse comme page"
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[12px] font-medium transition-colors ${
                fileBack ? "text-[#a78bfa] bg-[#1d1e22]" : "text-[#a1a1a6] hover:text-[#ececed] hover:bg-[#1d1e22]"
              }`}
            >
              <Bookmark size={13} strokeWidth={1.8} />
              Sauvegarder
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button className="inline-flex items-center gap-1 text-[11.5px] text-[#6b6c72] hover:text-[#a1a1a6] px-1.5 transition-colors">
              Local <ChevronDown size={10} strokeWidth={2} />
            </button>
            <button
              onClick={send}
              disabled={busy || !input.trim()}
              className="w-8 h-8 grid place-items-center rounded-lg text-white transition-all hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ background: ACCENT }}
            >
              <ArrowUp size={14} strokeWidth={2} />
            </button>
          </div>
        </div>
      </div>

      {isEmpty && (
        <>
          <div className="flex flex-wrap gap-1.5 mt-4 justify-center">
            {SUGGESTIONS.map(s => (
              <button
                key={s}
                onClick={() => { setInput(s); textareaRef.current?.focus(); }}
                className="px-2.5 py-1 rounded-full border border-[#212226] text-[12px] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
          <div className="mt-8 flex items-center justify-center gap-4 text-[11px] text-[#6b6c72]">
            <span className="flex items-center gap-1.5"><kbd className="px-1.5 py-0.5 rounded border border-[#2a2b31] bg-[#17181b] font-mono text-[10px] text-[#a1a1a6]">↵</kbd> envoyer</span>
            <span className="flex items-center gap-1.5"><kbd className="px-1.5 py-0.5 rounded border border-[#2a2b31] bg-[#17181b] font-mono text-[10px] text-[#a1a1a6]">⇧ ↵</kbd> nouvelle ligne</span>
          </div>
        </>
      )}
    </div>
  );

  return (
    <div className="flex flex-col h-full" style={{ background: "#0a0a0b" }}>
      {isEmpty ? (
        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className="mb-8">
            <div
              className="w-14 h-14 rounded-2xl grid place-items-center"
              style={{ background: "linear-gradient(135deg, rgba(139,92,246,.25), rgba(139,92,246,.05))", border: "1px solid rgba(139,92,246,.35)" }}
            >
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M4 6h4v4H4zM10 6h4v4h-4zM16 6h4v4h-4zM4 14h4v4H4zM16 14h4v4h-4z" fill="#8b5cf6" />
                <path d="M10 14h4v4h-4z" fill="#a78bfa" />
              </svg>
            </div>
          </div>
          {Composer}
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto ui-scroll">
            <div className="max-w-[760px] mx-auto px-5 py-6 space-y-5">
              {messages.map(msg => (
                <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  {msg.role === "user" ? (
                    <div
                      className="max-w-xl text-white text-[14px] rounded-2xl rounded-br-sm px-4 py-2.5 leading-relaxed"
                      style={{ background: ACCENT }}
                    >
                      {msg.content}
                    </div>
                  ) : (
                    <div
                      className="w-full text-[14px] leading-relaxed"
                      style={{
                        background: msg.role === "suggest" ? "rgba(234,179,8,0.04)" : "transparent",
                        border: msg.role === "suggest" ? "1px solid rgba(234,179,8,0.3)" : "none",
                        borderRadius: msg.role === "suggest" ? 12 : 0,
                        padding: msg.role === "suggest" ? "12px 16px" : 0,
                      }}
                    >
                      {msg.role === "suggest" && (
                        <p className="flex items-center gap-1.5 text-[11px] text-yellow-500/80 mb-2 font-medium uppercase tracking-wide">
                          <Lightbulb size={11} /> Analyse du wiki
                        </p>
                      )}
                      <div className="prose prose-invert prose-sm max-w-none">
                        <ReactMarkdown>{msg.content || (msg.loading ? "\u00a0" : "")}</ReactMarkdown>
                      </div>
                      {msg.loading && (
                        <span className="inline-block w-1.5 h-3.5 bg-current opacity-60 animate-pulse ml-0.5 align-middle" />
                      )}
                    </div>
                  )}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          </div>
          <div className="shrink-0 px-4 pb-4 pt-2" style={{ background: "linear-gradient(to top, #0a0a0b 70%, rgba(10,10,11,0))" }}>
            {Composer}
          </div>
        </>
      )}
    </div>
  );
}
