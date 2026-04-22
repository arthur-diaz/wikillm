import { useEffect, useState } from "react";
import { MessageSquare, BookOpen, Upload, Settings, PanelLeft, Network, ChevronDown, Plus } from "lucide-react";
import type { Panel } from "../App";
import type { Corpus } from "../CorpusContext";

const items: { id: Panel; icon: React.ElementType; label: string }[] = [
  { id: "chat",    icon: MessageSquare, label: "Chat" },
  { id: "library", icon: BookOpen,      label: "Bibliothèque" },
  { id: "graph",   icon: Network,       label: "Graph" },
  { id: "ingest",  icon: Upload,        label: "Ingest" },
];

type Props = {
  active: Panel;
  onChange: (p: Panel) => void;
  activeCorpus: string;
  corpusList: Corpus[];
  onSwitchCorpus: (id: string) => void;
};

export default function Sidebar({ active, onChange, activeCorpus, corpusList, onSwitchCorpus }: Props) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem("sb-collapsed") === "true"; } catch { return false; }
  });
  const [corpusOpen, setCorpusOpen] = useState(false);

  useEffect(() => {
    try { localStorage.setItem("sb-collapsed", String(collapsed)); } catch {}
  }, [collapsed]);

  // ⌘/Ctrl+B to toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setCollapsed(v => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const activeCorpusName = corpusList.find(c => c.id === activeCorpus)?.name ?? "—";

  const navItem = (id: Panel | "settings", icon: React.ElementType, label: string) => {
    const Icon = icon;
    const isActive = active === id;
    return (
      <button
        key={id}
        onClick={() => onChange(id as Panel)}
        title={collapsed ? label : undefined}
        className={`group w-full flex items-center gap-2.5 rounded-md transition-colors ${
          collapsed ? "h-9 justify-center" : "px-2 py-1.5"
        } ${
          isActive
            ? "bg-[#1d1e22] text-[#ececed]"
            : "text-[#a1a1a6] hover:text-[#ececed] hover:bg-[#1d1e22]"
        }`}
      >
        <Icon size={16} strokeWidth={1.7} className={`shrink-0 ${isActive ? "text-[#a78bfa]" : ""}`} />
        {!collapsed && <span className="text-[13px] font-medium truncate">{label}</span>}
      </button>
    );
  };

  return (
    <aside
      className="flex flex-col border-r border-[#212226] overflow-hidden shrink-0 transition-[width] duration-200"
      style={{ background: "#111113", width: collapsed ? 60 : 232 }}
    >
      {/* Top — brand + collapse */}
      <div className={`h-[52px] shrink-0 flex items-center ${collapsed ? "justify-center" : "justify-between px-3"}`}>
        <div className="w-7 h-7 grid place-items-center rounded-md shrink-0" style={{ background: "#8b5cf6" }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />
          </svg>
        </div>
        {!collapsed && (
          <button
            onClick={() => setCollapsed(true)}
            title="Réduire (⌘B)"
            className="w-7 h-7 grid place-items-center rounded-md text-[#6b6c72] hover:text-[#ececed] hover:bg-[#1d1e22] transition-colors"
          >
            <PanelLeft size={15} strokeWidth={1.6} />
          </button>
        )}
      </div>

      {/* Expand button when collapsed */}
      {collapsed && (
        <button
          onClick={() => setCollapsed(false)}
          title="Étendre (⌘B)"
          className="mx-auto mb-1 w-7 h-7 grid place-items-center rounded-md text-[#6b6c72] hover:text-[#ececed] hover:bg-[#1d1e22] transition-colors"
        >
          <PanelLeft size={15} strokeWidth={1.6} />
        </button>
      )}

      {/* Corpus selector (expanded only) */}
      {!collapsed && (
        <div className="px-3 mb-2 relative">
          <button
            onClick={() => setCorpusOpen(v => !v)}
            className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border border-[#212226] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
          >
            <span className="flex items-center gap-2 min-w-0">
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: "#34d399" }} />
              <span className="text-[12.5px] text-[#ececed] truncate">{activeCorpusName}</span>
            </span>
            <ChevronDown size={12} className={`text-[#6b6c72] transition-transform ${corpusOpen ? "rotate-180" : ""}`} />
          </button>

          {corpusOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setCorpusOpen(false)} />
              <div
                className="absolute left-3 right-3 top-full mt-1 z-20 rounded-md border border-[#2a2b31] overflow-hidden"
                style={{ background: "#17181b", boxShadow: "0 10px 30px rgba(0,0,0,.5)" }}
              >
                {corpusList.map(c => (
                  <button
                    key={c.id}
                    onClick={() => { onSwitchCorpus(c.id); setCorpusOpen(false); }}
                    className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] transition-colors ${
                      c.id === activeCorpus ? "text-[#ececed] bg-[#1d1e22]" : "text-[#a1a1a6] hover:text-[#ececed] hover:bg-[#1d1e22]"
                    }`}
                  >
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ background: c.id === activeCorpus ? "#34d399" : "#3a3b42" }}
                    />
                    <span className="truncate">{c.name}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 px-2 overflow-y-auto ui-scroll">
        {!collapsed && (
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#6b6c72] px-2 pt-3 pb-1.5">
            Espace
          </div>
        )}
        <div className="flex flex-col gap-0.5">
          {items.map(it => navItem(it.id, it.icon, it.label))}
        </div>
      </nav>

      {/* Bottom — settings + new */}
      <div className="px-2 pb-3 shrink-0 border-t border-[#212226] pt-2 mt-2">
        <div className="flex flex-col gap-0.5">
          {navItem("settings", Settings, "Réglages")}
          {!collapsed && (
            <button
              className="group w-full flex items-center gap-2.5 px-2 py-1.5 rounded-md text-[#a1a1a6] hover:text-[#ececed] hover:bg-[#1d1e22] transition-colors"
              title="Nouveau chat"
            >
              <Plus size={16} strokeWidth={1.7} className="shrink-0" />
              <span className="text-[13px] font-medium">Nouveau chat</span>
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
