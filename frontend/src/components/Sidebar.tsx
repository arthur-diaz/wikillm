import { MessageSquare, BookOpen, Upload, Settings } from "lucide-react";
import type { Panel } from "../App";
import type { Corpus } from "../CorpusContext";

const items: { id: Panel; icon: React.ElementType; label: string }[] = [
  { id: "chat",     icon: MessageSquare, label: "Chat" },
  { id: "library",  icon: BookOpen,      label: "Bibliothèque" },
  { id: "ingest",   icon: Upload,        label: "Ingest" },
];

type Props = {
  active: Panel;
  onChange: (p: Panel) => void;
  activeCorpus: string;
  corpusList: Corpus[];
  onSwitchCorpus: (id: string) => void;
};

export default function Sidebar({ active, onChange, activeCorpus, corpusList, onSwitchCorpus }: Props) {
  return (
    <nav className="w-14 lg:w-52 flex flex-col border-r border-gray-800 flex-shrink-0" style={{ background: "#161b22" }}>
      {/* Header + corpus selector */}
      <div className="px-3 pt-4 pb-3 border-b border-gray-800">
        <p className="hidden lg:block text-xs font-bold text-blue-400 mb-2 px-1">LLM Wiki</p>
        <select
          value={activeCorpus}
          onChange={e => onSwitchCorpus(e.target.value)}
          className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-blue-500 cursor-pointer"
          title="Corpus actif"
        >
          {corpusList.map(c => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* Nav items */}
      <div className="flex-1 py-2">
        {items.map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => onChange(id)}
            className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
              active === id
                ? "bg-blue-900/30 text-blue-300 border-r-2 border-blue-400"
                : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/50"
            }`}
          >
            <Icon size={17} className="flex-shrink-0" />
            <span className="hidden lg:block">{label}</span>
          </button>
        ))}
      </div>

      {/* Settings pinned at bottom */}
      <div className="border-t border-gray-800 py-2">
        <button
          onClick={() => onChange("settings")}
          className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
            active === "settings"
              ? "bg-blue-900/30 text-blue-300 border-r-2 border-blue-400"
              : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/50"
          }`}
        >
          <Settings size={17} className="flex-shrink-0" />
          <span className="hidden lg:block">Réglages</span>
        </button>
      </div>
    </nav>
  );
}
