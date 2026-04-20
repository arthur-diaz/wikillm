import { useEffect, useRef, useState } from "react";
import { Search, Network, ArrowLeft, Tag } from "lucide-react";
import ReactMarkdown from "react-markdown";
import GraphPanel from "./GraphPanel";

type Page = { slug: string; type: string; name: string; tags: string[]; last_updated: string };
type SearchResult = { slug: string; score: number; snippet: string };
type PageContent = { slug: string; meta: Record<string, unknown>; content: string };

const TYPE_LABELS: Record<string, string> = { source: "Sources", entity: "Entités", concept: "Concepts" };
const TYPE_DOT: Record<string, string> = { source: "bg-orange-400", entity: "bg-violet-400", concept: "bg-emerald-400" };

export default function LibraryPanel() {
  const [pages, setPages] = useState<Page[]>([]);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<PageContent | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [mobileView, setMobileView] = useState<"list" | "detail">("list");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch("/api/pages").then(r => r.json()).then(setPages);
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) { setResults(null); return; }
    debounceRef.current = setTimeout(async () => {
      const data = await fetch(`/api/search?q=${encodeURIComponent(query)}&n=30`).then(r => r.json());
      setResults(data.results);
    }, 260);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  const openPage = async (slug: string) => {
    setSelected(slug);
    setShowGraph(false);
    setMobileView("detail");
    const data = await fetch(`/api/pages/${slug}`).then(r => r.json());
    setContent(data);
  };

  const displayed: { slug: string; name: string; type: string; tags: string[]; snippet?: string }[] = results
    ? results.map(r => {
        const p = pages.find(x => x.slug === r.slug);
        return { slug: r.slug, name: p?.name ?? r.slug, type: p?.type ?? "", tags: p?.tags ?? [], snippet: r.snippet };
      })
    : pages.filter(p => typeFilter === "all" || p.type === typeFilter);

  const allTags = [...new Set(pages.flatMap(p => p.tags))].sort();

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left panel — list */}
      <div className={`flex flex-col border-r border-gray-800 flex-shrink-0 ${
        mobileView === "detail" ? "hidden lg:flex" : "flex"
      } w-full lg:w-72`} style={{ background: "#161b22" }}>
        {/* Search */}
        <div className="p-3 border-b border-gray-800">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-600" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Rechercher…"
              className="w-full bg-gray-900 border border-gray-700 rounded pl-8 pr-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* Type filter + graph toggle */}
        {!results && (
          <div className="flex items-center gap-1 px-3 py-2 border-b border-gray-800 overflow-x-auto">
            {["all", "source", "entity", "concept"].map(t => (
              <button key={t} onClick={() => setTypeFilter(t)}
                className={`text-xs px-2.5 py-1 rounded-full whitespace-nowrap transition-colors ${
                  typeFilter === t ? "bg-blue-600 text-white" : "text-gray-500 hover:text-gray-300 bg-gray-800"
                }`}>
                {t === "all" ? "Tout" : TYPE_LABELS[t]}
              </button>
            ))}
            <button onClick={() => { setShowGraph(v => !v); setMobileView("detail"); }}
              className={`ml-auto text-xs px-2.5 py-1 rounded-full flex items-center gap-1 flex-shrink-0 transition-colors ${
                showGraph ? "bg-blue-600 text-white" : "text-gray-500 hover:text-gray-300 bg-gray-800"
              }`}>
              <Network size={11} /> Graph
            </button>
          </div>
        )}

        {/* Page list */}
        <div className="flex-1 overflow-y-auto">
          {displayed.length === 0 && (
            <p className="text-xs text-gray-600 text-center py-8">Aucune page.</p>
          )}
          {displayed.map(p => (
            <button key={p.slug} onClick={() => openPage(p.slug)}
              className={`w-full text-left px-3 py-2.5 border-b border-gray-800/50 transition-colors ${
                selected === p.slug ? "bg-blue-900/20 border-l-2 border-l-blue-500" : "hover:bg-gray-800/40"
              }`}>
              <div className="flex items-center gap-2">
                {p.type && <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${TYPE_DOT[p.type] ?? "bg-gray-600"}`} />}
                <span className="text-sm text-gray-200 truncate">{p.name}</span>
              </div>
              {p.snippet && (
                <p className="text-xs text-gray-600 mt-0.5 truncate pl-3.5">{p.snippet}</p>
              )}
              {p.tags.length > 0 && (
                <div className="flex gap-1 mt-1 pl-3.5 flex-wrap">
                  {p.tags.slice(0, 3).map(tag => (
                    <span key={tag} className="text-xs bg-gray-800 text-gray-500 px-1.5 py-0.5 rounded">{tag}</span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>

        <div className="px-3 py-2 border-t border-gray-800">
          <p className="text-xs text-gray-700">{pages.length} page{pages.length !== 1 ? "s" : ""}</p>
        </div>
      </div>

      {/* Right panel — content or graph */}
      <div className={`flex-1 flex flex-col overflow-hidden ${
        mobileView === "list" ? "hidden lg:flex" : "flex"
      }`}>
        {/* Mobile back button */}
        <div className="lg:hidden flex items-center border-b border-gray-800 px-3 py-2" style={{ background: "#161b22" }}>
          <button onClick={() => setMobileView("list")} className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-200">
            <ArrowLeft size={15} /> Retour
          </button>
        </div>

        {showGraph ? (
          <GraphPanel />
        ) : content ? (
          <div className="flex-1 overflow-y-auto p-6">
            <div className="max-w-2xl mx-auto">
              <div className="flex items-center gap-2 mb-1">
                {content.meta.type ? (
                  <span className={`w-2 h-2 rounded-full ${TYPE_DOT[String(content.meta.type)] ?? "bg-gray-600"}`} />
                ) : null}
                <h1 className="text-lg font-semibold text-gray-200">
                  {String(content.meta.name ?? content.meta.title ?? content.slug)}
                </h1>
              </div>
              {(content.meta.tags as string[] | undefined)?.length ? (
                <div className="flex gap-1.5 mb-4 flex-wrap">
                  {(content.meta.tags as string[]).map(t => (
                    <span key={t} className="text-xs bg-gray-800 text-gray-500 px-2 py-0.5 rounded flex items-center gap-1">
                      <Tag size={9} />{t}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown>{content.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-3 text-gray-700">
            <BookIcon />
            <p className="text-sm">Sélectionne une page ou affiche le graph.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function BookIcon() {
  return (
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
    </svg>
  );
}
