import { useEffect, useRef, useState } from "react";
import { Search, Network, ArrowLeft, Tag, Pencil, Trash2, Plus, X, Save, Loader2, BookOpen } from "lucide-react";
import ReactMarkdown from "react-markdown";
import GraphPanel from "./GraphPanel";
import { COLORS, TYPE_LABELS, TextInput, hexToRgba, ACCENT } from "./ui/design";

type Page = { slug: string; type: string; name: string; tags: string[]; last_updated: string };
type SearchResult = { slug: string; score: number; snippet: string };
type PageContent = { slug: string; meta: Record<string, unknown>; content: string };

function autoSlug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

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

  const [editMode, setEditMode] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);

  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [showNewForm, setShowNewForm] = useState(false);
  const [newType, setNewType] = useState<"concept" | "entity" | "source">("concept");
  const [newName, setNewName] = useState("");
  const [newTags, setNewTags] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const refreshPages = () =>
    fetch("/api/pages").then(r => r.json()).then(setPages);

  useEffect(() => { refreshPages(); }, []);

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
    setSelected(slug); setShowGraph(false); setMobileView("detail");
    setEditMode(false); setDeleteConfirm(false);
    const data = await fetch(`/api/pages/${slug}`).then(r => r.json());
    setContent(data);
  };

  const startEdit = () => { if (!content) return; setEditText(content.content); setEditMode(true); };
  const cancelEdit = () => setEditMode(false);

  const saveEdit = async () => {
    if (!selected || !content) return;
    setSaving(true);
    try {
      const res = await fetch(`/api/pages/${selected}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editText }),
      });
      if (res.ok) {
        const { last_updated } = await res.json();
        setContent({ ...content, content: editText, meta: { ...content.meta, last_updated } });
        setEditMode(false);
      }
    } finally { setSaving(false); }
  };

  const deletePage = async () => {
    if (!selected) return;
    setDeleting(true);
    try {
      const res = await fetch(`/api/pages/${selected}`, { method: "DELETE" });
      if (res.ok) {
        setSelected(null); setContent(null); setDeleteConfirm(false); setMobileView("list");
        await refreshPages();
      }
    } finally { setDeleting(false); }
  };

  const createPage = async () => {
    if (!newName.trim()) { setCreateError("Le nom est requis."); return; }
    setCreating(true); setCreateError("");
    try {
      const tags = newTags.split(",").map(t => t.trim()).filter(Boolean);
      const res = await fetch("/api/pages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: newType, name: newName.trim(), tags }),
      });
      if (res.ok) {
        const data = await res.json();
        setShowNewForm(false); setNewName(""); setNewTags(""); setNewType("concept");
        await refreshPages();
        setSelected(data.slug); setContent(data); setShowGraph(false); setMobileView("detail");
        setEditText(data.content); setEditMode(true);
      } else {
        const err = await res.json();
        setCreateError(err.detail ?? "Erreur lors de la création.");
      }
    } finally { setCreating(false); }
  };

  const displayed = results
    ? results.map(r => {
        const p = pages.find(x => x.slug === r.slug);
        return { slug: r.slug, name: p?.name ?? r.slug, type: p?.type ?? "", tags: p?.tags ?? [], snippet: r.snippet };
      })
    : pages.filter(p => typeFilter === "all" || p.type === typeFilter).map(p => ({ ...p, snippet: undefined as string | undefined }));

  return (
    <div className="flex h-full overflow-hidden" style={{ background: "#0a0a0b" }}>
      {/* ── Left panel ── */}
      <div
        className={`flex flex-col border-r border-[#212226] flex-shrink-0 ${
          mobileView === "detail" ? "hidden lg:flex" : "flex"
        } w-full lg:w-72`}
        style={{ background: "#111113" }}
      >
        {/* Search + new */}
        <div className="p-3 border-b border-[#212226] flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#6b6c72]" />
            <TextInput value={query} onChange={e => setQuery(e.target.value)} placeholder="Rechercher…" className="!pl-8" />
          </div>
          <button
            onClick={() => { setShowNewForm(v => !v); setShowGraph(false); }}
            className={`flex-shrink-0 w-[30px] h-[30px] grid place-items-center rounded-md border transition-colors ${
              showNewForm
                ? "border-[#8b5cf6] text-white"
                : "border-[#212226] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22]"
            }`}
            style={showNewForm ? { background: ACCENT } : { background: "#17181b" }}
            title="Nouvelle page"
          >
            {showNewForm ? <X size={14} /> : <Plus size={14} />}
          </button>
        </div>

        {/* New page form */}
        {showNewForm && (
          <div className="p-3 border-b border-[#212226]" style={{ background: "#0d0d0f" }}>
            <p className="text-[11px] font-medium text-[#a1a1a6] mb-2 uppercase tracking-wide">Nouvelle page</p>
            <div className="flex gap-1 mb-2">
              {(["concept", "entity", "source"] as const).map(t => {
                const col = COLORS[t];
                const active = newType === t;
                return (
                  <button
                    key={t}
                    onClick={() => setNewType(t)}
                    className="text-[11px] px-2.5 py-[3px] rounded-full border transition-colors font-medium"
                    style={{
                      background: active ? hexToRgba(col, 0.12) : "transparent",
                      color: active ? col : "#6b6c72",
                      borderColor: active ? hexToRgba(col, 0.4) : "#212226",
                    }}
                  >
                    {TYPE_LABELS[t]}
                  </button>
                );
              })}
            </div>
            <TextInput
              value={newName}
              onChange={e => { setNewName(e.target.value); setCreateError(""); }}
              placeholder="Nom…"
              className="mb-1.5"
              onKeyDown={e => { if (e.key === "Enter") createPage(); }}
            />
            {newName && <p className="text-[11px] text-[#6b6c72] mb-1.5 font-mono">{autoSlug(newName)}</p>}
            <TextInput value={newTags} onChange={e => setNewTags(e.target.value)} placeholder="Tags (séparés par virgule)…" className="mb-2" />
            {createError && <p className="text-[11px] text-red-400 mb-1.5">{createError}</p>}
            <div className="flex gap-1.5">
              <button
                onClick={createPage}
                disabled={creating || !newName.trim()}
                className="flex-1 flex items-center justify-center gap-1 text-[12px] py-1 text-white rounded-md disabled:opacity-50 hover:brightness-110 transition-all"
                style={{ background: ACCENT }}
              >
                {creating ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                Créer
              </button>
              <button
                onClick={() => { setShowNewForm(false); setCreateError(""); setNewName(""); setNewTags(""); }}
                className="text-[12px] px-2 py-1 text-[#a1a1a6] hover:text-[#ececed] rounded-md border border-[#212226] hover:border-[#2a2b31] transition-colors"
              >
                Annuler
              </button>
            </div>
          </div>
        )}

        {/* Type filter + graph */}
        {!results && !showNewForm && (
          <div className="flex items-center gap-1 px-3 py-2 border-b border-[#212226] overflow-x-auto ui-scroll">
            {["all", "source", "entity", "concept"].map(t => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`text-[11px] px-2.5 py-1 rounded-full whitespace-nowrap transition-colors ${
                  typeFilter === t
                    ? "text-[#ececed] bg-[#1d1e22]"
                    : "text-[#6b6c72] hover:text-[#a1a1a6] hover:bg-[#1d1e22]"
                }`}
              >
                {t === "all" ? "Tout" : TYPE_LABELS[t]}
              </button>
            ))}
            <button
              onClick={() => { setShowGraph(v => !v); setMobileView("detail"); }}
              className={`ml-auto text-[11px] px-2.5 py-1 rounded-full flex items-center gap-1 flex-shrink-0 transition-colors ${
                showGraph ? "text-[#a78bfa] bg-[#1d1e22]" : "text-[#6b6c72] hover:text-[#a1a1a6] hover:bg-[#1d1e22]"
              }`}
            >
              <Network size={11} /> Graph
            </button>
          </div>
        )}

        {/* Page list */}
        <div className="flex-1 overflow-y-auto ui-scroll">
          {displayed.length === 0 && (
            <p className="text-[11px] text-[#6b6c72] text-center py-8">Aucune page.</p>
          )}
          {displayed.map(p => (
            <button
              key={p.slug}
              onClick={() => openPage(p.slug)}
              className={`w-full text-left px-3 py-2.5 border-b border-[#212226]/50 transition-colors relative ${
                selected === p.slug ? "bg-[#1d1e22]" : "hover:bg-[#17181b]"
              }`}
            >
              {selected === p.slug && <span className="absolute left-0 top-2 bottom-2 w-[2px] bg-[#8b5cf6] rounded-r" />}
              <div className="flex items-center gap-2">
                {p.type && (
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: COLORS[p.type as keyof typeof COLORS] ?? "#3a3b42" }} />
                )}
                <span className="text-[13px] text-[#ececed] truncate">{p.name}</span>
              </div>
              {p.snippet && (
                <p className="text-[11px] text-[#6b6c72] mt-0.5 truncate pl-3.5">{p.snippet}</p>
              )}
              {p.tags.length > 0 && (
                <div className="flex gap-1 mt-1 pl-3.5 flex-wrap">
                  {p.tags.slice(0, 3).map(tag => (
                    <span key={tag} className="text-[10.5px] bg-[#17181b] text-[#a1a1a6] border border-[#212226] px-1.5 py-0.5 rounded">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </button>
          ))}
        </div>

        <div className="px-3 py-2 border-t border-[#212226]">
          <p className="text-[11px] text-[#6b6c72]">{pages.length} page{pages.length !== 1 ? "s" : ""}</p>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className={`flex-1 flex flex-col overflow-hidden ${mobileView === "list" ? "hidden lg:flex" : "flex"}`}>
        <div className="lg:hidden flex items-center border-b border-[#212226] px-3 py-2" style={{ background: "#111113" }}>
          <button onClick={() => setMobileView("list")} className="flex items-center gap-1.5 text-[13px] text-[#a1a1a6] hover:text-[#ececed]">
            <ArrowLeft size={15} /> Retour
          </button>
        </div>

        {showGraph ? (
          <GraphPanel onOpenPage={slug => openPage(slug)} />
        ) : content ? (
          <div className="flex-1 overflow-y-auto p-8 ui-scroll">
            <div className="max-w-2xl mx-auto">
              <div className="flex items-start justify-between gap-3 mb-1">
                <div className="flex items-center gap-2 min-w-0">
                  {content.meta.type ? (
                    <span className="w-2 h-2 rounded-full flex-shrink-0 mt-1.5" style={{ background: COLORS[String(content.meta.type) as keyof typeof COLORS] ?? "#3a3b42" }} />
                  ) : null}
                  <h1 className="text-[18px] font-semibold text-[#ececed] leading-snug">
                    {String(content.meta.name ?? content.meta.title ?? content.slug)}
                  </h1>
                </div>

                {!editMode && !deleteConfirm && (
                  <div className="flex items-center gap-1.5 flex-shrink-0 mt-0.5">
                    <button
                      onClick={startEdit}
                      className="flex items-center gap-1 text-[12px] px-2 py-1 rounded-md border border-[#212226] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
                    >
                      <Pencil size={11} /> Modifier
                    </button>
                    <button
                      onClick={() => setDeleteConfirm(true)}
                      className="w-[28px] h-[28px] grid place-items-center rounded-md text-[#6b6c72] hover:text-red-400 hover:bg-[#1d1e22] transition-colors"
                      title="Supprimer"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                )}
              </div>

              {(content.meta.tags as string[] | undefined)?.length ? (
                <div className="flex gap-1.5 mb-4 flex-wrap">
                  {(content.meta.tags as string[]).map(t => (
                    <span key={t} className="text-[11px] bg-[#17181b] border border-[#212226] text-[#a1a1a6] px-2 py-0.5 rounded flex items-center gap-1">
                      <Tag size={9} />{t}
                    </span>
                  ))}
                </div>
              ) : <div className="mb-3" />}

              {Object.keys(content.meta).length > 0 && !editMode && (
                <details className="mb-4 group">
                  <summary className="text-[11px] text-[#6b6c72] cursor-pointer hover:text-[#a1a1a6] select-none list-none flex items-center gap-1">
                    <span className="group-open:rotate-90 inline-block transition-transform">▶</span> Métadonnées
                  </summary>
                  <pre className="mt-2 text-[11px] text-[#a1a1a6] bg-[#111113] rounded-md p-3 overflow-x-auto whitespace-pre-wrap border border-[#212226] ui-scroll">
{Object.entries(content.meta).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n")}
                  </pre>
                </details>
              )}

              {deleteConfirm && (
                <div className="mb-4 flex items-center gap-3 p-3 rounded-md border border-red-900/60 bg-red-950/20">
                  <p className="text-[13px] text-[#ececed] flex-1">Supprimer définitivement cette page ?</p>
                  <button
                    onClick={deletePage}
                    disabled={deleting}
                    className="flex items-center gap-1 text-[12px] px-2.5 py-1 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white rounded-md transition-colors"
                  >
                    {deleting ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                    Supprimer
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(false)}
                    className="text-[12px] px-2.5 py-1 text-[#a1a1a6] hover:text-[#ececed] border border-[#212226] hover:border-[#2a2b31] rounded-md transition-colors"
                  >
                    Annuler
                  </button>
                </div>
              )}

              {editMode ? (
                <div className="flex flex-col gap-3">
                  <textarea
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    className="w-full min-h-[400px] bg-[#111113] border border-[#212226] focus:border-[#2a2b31] rounded-md p-3 text-[13px] text-[#ececed] font-mono resize-y outline-none leading-relaxed ui-scroll transition-colors"
                    spellCheck={false}
                  />
                  <div className="flex items-center gap-2">
                    <button
                      onClick={saveEdit}
                      disabled={saving}
                      className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 text-white rounded-md disabled:opacity-50 hover:brightness-110 transition-all"
                      style={{ background: ACCENT }}
                    >
                      {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                      Sauvegarder
                    </button>
                    <button
                      onClick={cancelEdit}
                      className="text-[13px] px-3 py-1.5 text-[#a1a1a6] hover:text-[#ececed] border border-[#212226] hover:border-[#2a2b31] rounded-md transition-colors"
                    >
                      Annuler
                    </button>
                  </div>
                </div>
              ) : (
                <div className="prose prose-invert prose-sm max-w-none">
                  <ReactMarkdown>{content.content}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-3 text-[#6b6c72]">
            <div className="w-12 h-12 rounded-xl border border-[#212226] bg-[#111113] grid place-items-center">
              <BookOpen size={22} strokeWidth={1.5} />
            </div>
            <p className="text-[13px]">Sélectionne une page ou affiche le graph.</p>
          </div>
        )}
      </div>
    </div>
  );
}
