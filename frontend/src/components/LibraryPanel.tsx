import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, Plus, Pencil, Trash2, Loader2, Save, X, Tag, BookOpen, ChevronRight,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { COLORS, TYPE_LABELS, hexToRgba, ACCENT } from "./ui/design";

type Page = { slug: string; type: string; name: string; tags: string[]; last_updated: string };
type SearchResult = { slug: string; score: number; snippet: string };
type PageContent = { slug: string; meta: Record<string, unknown>; content: string };

const MONTHS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "aoû", "sep", "oct", "nov", "déc"];

function formatDate(iso?: string) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  } catch { return ""; }
}

function autoSlug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export default function LibraryPanel({ initialPage }: { initialPage?: string }) {
  const [pages, setPages] = useState<Page[]>([]);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<PageContent | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
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

  const refreshPages = () => fetch("/api/pages").then(r => r.json()).then(setPages);
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

  const openPage = useCallback(async (slug: string) => {
    setSelected(slug);
    setEditMode(false);
    setDeleteConfirm(false);
    setContent(null);
    setContentLoading(true);
    try {
      const data = await fetch(`/api/pages/${slug}`).then(r => r.json());
      setContent(data);
    } finally {
      setContentLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialPage && pages.length > 0) openPage(initialPage);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages]);

  const startEdit = () => {
    if (!content) return;
    setEditText(content.content);
    setEditMode(true);
  };

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
        setSelected(null);
        setContent(null);
        setDeleteConfirm(false);
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
        setSelected(data.slug); setContent(data);
        setEditText(data.content); setEditMode(true);
      } else {
        const err = await res.json();
        setCreateError(err.detail ?? "Erreur lors de la création.");
      }
    } finally { setCreating(false); }
  };

  const displayed = useMemo(() => {
    const raw = results
      ? results.map(r => {
          const p = pages.find(x => x.slug === r.slug);
          return { slug: r.slug, name: p?.name ?? r.slug, type: p?.type ?? "", tags: p?.tags ?? [], last_updated: p?.last_updated ?? "" };
        })
      : pages.filter(p => typeFilter === "all" || p.type === typeFilter);
    const seen = new Set<string>();
    return raw.filter(p => { if (seen.has(p.slug)) return false; seen.add(p.slug); return true; });
  }, [results, pages, typeFilter]);

  const linkedSlugs = useMemo(() => {
    if (!content) return [];
    const matches = [...content.content.matchAll(/\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/g)];
    return [...new Set(matches.map(m => m[1].trim()))];
  }, [content]);

  const linkedPages = useMemo(
    () => linkedSlugs.map(slug => pages.find(p => p.slug === slug)).filter((p): p is Page => Boolean(p)),
    [linkedSlugs, pages]
  );

  const processedContent = useMemo(() => {
    if (!content) return "";
    return content.content.replace(
      /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g,
      (_, slug, label) => `[${label ?? slug}](#wiki:${encodeURIComponent(slug)})`
    );
  }, [content]);

  const pageName = content ? String(content.meta.name ?? content.meta.title ?? content.slug) : "";
  const pageType = content ? String(content.meta.type ?? "") : "";
  const pageTags = (content?.meta.tags as string[] | undefined) ?? [];
  const pageTypeColor = COLORS[pageType as keyof typeof COLORS];
  const lastUpdated = content ? formatDate(String(content.meta.last_updated ?? "")) : "";

  return (
    <div className="relative flex h-full overflow-hidden" style={{ background: "#0d1117" }}>

      {/* ── Left column ── */}
      <div
        className="flex flex-col flex-shrink-0 border-r border-[#212226]"
        style={{ width: 320, background: "#111113" }}
      >
        {/* Search */}
        <div className="px-3 pt-3 pb-2.5 border-b border-[#212226]">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[#6b6c72] pointer-events-none" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Rechercher..."
              className="w-full bg-[#0d1117] border border-[#212226] rounded-md pl-8 pr-3 py-[7px] text-[12px] text-[#ececed] placeholder:text-[#6b6c72] outline-none focus:border-[#2a2b31] transition-colors"
            />
          </div>
        </div>

        {/* Type filter tabs */}
        <div className="px-2 py-1.5 border-b border-[#212226] flex items-center gap-0.5">
          {(["all", "source", "entity", "concept"] as const).map(t => {
            const active = typeFilter === t && !results;
            const col = t !== "all" ? COLORS[t] : null;
            return (
              <button
                key={t}
                onClick={() => { setTypeFilter(t); setResults(null); setQuery(""); }}
                className={`text-[11px] px-2.5 py-[5px] rounded-md transition-colors font-medium ${
                  active ? "bg-[#1d1e22] text-[#ececed]" : "text-[#6b6c72] hover:text-[#a1a1a6] hover:bg-[#1d1e22]"
                }`}
                style={active && col ? { color: col } : undefined}
              >
                {t === "all" ? "Tout" : TYPE_LABELS[t]}
              </button>
            );
          })}
          <span className="ml-auto text-[10px] text-[#3a3b42] pr-1 tabular-nums">{displayed.length}</span>
        </div>

        {/* Page list */}
        <div className="flex-1 overflow-y-auto ui-scroll">
          {displayed.length === 0 && (
            <p className="text-[11px] text-[#6b6c72] text-center py-10">Aucune page.</p>
          )}
          {displayed.map(p => (
            <button
              key={p.slug}
              onClick={() => openPage(p.slug)}
              className={`relative w-full flex items-center gap-3 px-4 py-3 border-b border-[#212226]/50 text-left transition-colors group ${
                selected === p.slug ? "bg-[#1d1e22]" : "hover:bg-[#1d1e22]"
              }`}
              style={{ minHeight: 56 }}
            >
              {selected === p.slug && (
                <span
                  className="absolute left-0 top-2 bottom-2 w-[2px] rounded-r"
                  style={{ background: ACCENT }}
                />
              )}
              <span
                className="w-2 h-2 rounded-full flex-shrink-0 mt-0.5"
                style={{ background: COLORS[p.type as keyof typeof COLORS] ?? "#3a3b42" }}
              />
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-[#ececed] truncate leading-snug">{p.name}</div>
                {p.tags.length > 0 && (
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {p.tags.slice(0, 3).map(tag => (
                      <span
                        key={tag}
                        className="text-[10px] px-1.5 py-[1px] rounded bg-[#0d1117] text-[#6b6c72] border border-[#212226] leading-none"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <span className="text-[11px] text-[#3a3b42] group-hover:text-[#6b6c72] flex-shrink-0 transition-colors whitespace-nowrap">
                {formatDate(p.last_updated)}
              </span>
            </button>
          ))}
        </div>

        {/* New page */}
        <div className="p-3 border-t border-[#212226]">
          <button
            onClick={() => setShowNewForm(true)}
            className="w-full flex items-center justify-center gap-1.5 text-[12px] py-[7px] rounded-md border border-[#212226] text-[#6b6c72] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
          >
            <Plus size={12} /> Nouvelle page
          </button>
        </div>
      </div>

      {/* ── Right column ── */}
      <div className="flex-1 flex flex-col overflow-hidden relative" style={{ background: "#0d1117" }}>

        {contentLoading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 size={20} className="animate-spin text-[#6b6c72]" />
          </div>

        ) : content ? (
          <>
            {/* Breadcrumb + actions */}
            <div
              className="flex items-center px-7 border-b border-[#212226] flex-shrink-0"
              style={{ minHeight: 48, background: "#0d1117" }}
            >
              <div className="flex items-center gap-1 text-[12px] text-[#6b6c72] flex-1 min-w-0 py-3">
                <span className="hidden sm:inline">Bibliothèque</span>
                <ChevronRight size={11} className="flex-shrink-0 text-[#2a2b31] hidden sm:block" />
                <span className="text-[#a1a1a6] truncate">{pageName}</span>
              </div>

              {!editMode && !deleteConfirm && (
                <div className="flex items-center gap-1.5 flex-shrink-0 ml-3 py-3">
                  <button
                    onClick={startEdit}
                    className="flex items-center gap-1 text-[12px] px-2.5 py-1.5 rounded-md border border-[#212226] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
                  >
                    <Pencil size={11} /> Modifier
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(true)}
                    className="w-[30px] h-[30px] grid place-items-center rounded-md text-[#6b6c72] hover:text-red-400 hover:bg-red-950/20 border border-transparent hover:border-red-900/30 transition-colors"
                    title="Supprimer"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              )}

              {deleteConfirm && (
                <div className="flex items-center gap-2 flex-shrink-0 ml-3 py-3">
                  <span className="text-[12px] text-[#ececed]">Supprimer ?</span>
                  <button
                    onClick={deletePage}
                    disabled={deleting}
                    className="flex items-center gap-1 text-[12px] px-2.5 py-1 bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white rounded-md transition-colors"
                  >
                    {deleting ? <Loader2 size={10} className="animate-spin" /> : null}
                    Oui
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(false)}
                    className="text-[12px] px-2.5 py-1 text-[#a1a1a6] hover:text-[#ececed] border border-[#212226] rounded-md transition-colors"
                  >
                    Non
                  </button>
                </div>
              )}
            </div>

            {/* Content area */}
            <div className="flex-1 overflow-y-auto ui-scroll">
              {editMode ? (
                <div className="px-8 py-6 flex flex-col gap-3 h-full">
                  <textarea
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    className="flex-1 min-h-[400px] bg-[#111113] border border-[#212226] focus:border-[#2a2b31] rounded-md p-4 text-[13px] text-[#ececed] font-mono resize-none outline-none leading-relaxed ui-scroll transition-colors"
                    spellCheck={false}
                  />
                  <div className="flex items-center gap-2 flex-shrink-0 pb-4">
                    <button
                      onClick={saveEdit}
                      disabled={saving}
                      className="flex items-center gap-1.5 text-[13px] px-4 py-2 text-white rounded-md disabled:opacity-50 hover:brightness-110 transition-all"
                      style={{ background: ACCENT }}
                    >
                      {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                      Sauvegarder
                    </button>
                    <button
                      onClick={() => setEditMode(false)}
                      className="text-[13px] px-4 py-2 text-[#a1a1a6] hover:text-[#ececed] border border-[#212226] hover:border-[#2a2b31] rounded-md transition-colors"
                    >
                      Annuler
                    </button>
                  </div>
                </div>
              ) : (
                <div className="px-8 py-8">
                  <div className="max-w-2xl mx-auto">

                    {/* Title */}
                    <h1 className="text-[26px] font-bold text-[#ececed] leading-tight mb-4">
                      {pageName}
                    </h1>

                    {/* Type badge + tags + date */}
                    <div className="flex items-center gap-2 flex-wrap mb-8">
                      {pageTypeColor && (
                        <span
                          className="text-[11px] px-2.5 py-1 rounded-full font-medium border"
                          style={{
                            color: pageTypeColor,
                            background: hexToRgba(pageTypeColor, 0.1),
                            borderColor: hexToRgba(pageTypeColor, 0.28),
                          }}
                        >
                          {TYPE_LABELS[pageType] ?? pageType}
                        </span>
                      )}
                      {pageTags.map(tag => (
                        <span
                          key={tag}
                          className="text-[11px] px-2.5 py-1 rounded-full bg-[#1d1e22] border border-[#212226] text-[#a1a1a6] flex items-center gap-1"
                        >
                          <Tag size={9} />{tag}
                        </span>
                      ))}
                      {lastUpdated && (
                        <span className="ml-auto text-[11px] text-[#6b6c72]">
                          Mis à jour le {lastUpdated}
                        </span>
                      )}
                    </div>

                    {/* Markdown */}
                    <div className="prose prose-invert prose-sm max-w-none">
                      <ReactMarkdown
                        components={{
                          a: ({ href, children }) => {
                            if (href?.startsWith("#wiki:")) {
                              const slug = decodeURIComponent(href.slice(6));
                              return (
                                <button
                                  onClick={() => openPage(slug)}
                                  className="text-blue-400 hover:text-blue-300 underline cursor-pointer bg-transparent border-none p-0"
                                  style={{ font: "inherit" }}
                                >
                                  {children}
                                </button>
                              );
                            }
                            return (
                              <a href={href} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">
                                {children}
                              </a>
                            );
                          },
                        }}
                      >
                        {processedContent}
                      </ReactMarkdown>
                    </div>

                    {/* Pages liées */}
                    {linkedPages.length > 0 && (
                      <div className="mt-10 pt-6 border-t border-[#212226]">
                        <p className="text-[11px] font-semibold uppercase tracking-wider text-[#6b6c72] mb-4">
                          Pages liées
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {linkedPages.map(p => (
                            <button
                              key={p.slug}
                              onClick={() => openPage(p.slug)}
                              className="flex items-center gap-2 text-[12px] px-3 py-1.5 rounded-full border border-[#212226] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] hover:bg-[#1d1e22] transition-colors"
                            >
                              <span
                                className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                                style={{ background: COLORS[p.type as keyof typeof COLORS] ?? "#3a3b42" }}
                              />
                              {p.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                  </div>
                </div>
              )}
            </div>
          </>

        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-[#3a3b42]">
            <BookOpen size={28} strokeWidth={1.2} />
            <p className="text-[13px] text-[#6b6c72]">Sélectionne une page</p>
          </div>
        )}

      </div>

      {/* New page modal */}
      {showNewForm && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center"
          style={{ background: "rgba(0,0,0,0.65)", backdropFilter: "blur(4px)" }}
          onClick={e => { if (e.target === e.currentTarget) { setShowNewForm(false); setCreateError(""); setNewName(""); setNewTags(""); } }}
        >
          <div
            className="w-[400px] rounded-xl border border-[#212226] p-6 flex flex-col gap-4"
            style={{ background: "#17181b", boxShadow: "0 24px 64px rgba(0,0,0,0.7)" }}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-[14px] font-semibold text-[#ececed]">Nouvelle page</h3>
              <button
                onClick={() => { setShowNewForm(false); setCreateError(""); setNewName(""); setNewTags(""); }}
                className="w-[26px] h-[26px] grid place-items-center rounded-md text-[#6b6c72] hover:text-[#ececed] hover:bg-[#1d1e22] transition-colors"
              >
                <X size={13} />
              </button>
            </div>

            <div className="flex gap-1.5">
              {(["concept", "entity", "source"] as const).map(t => {
                const col = COLORS[t];
                const active = newType === t;
                return (
                  <button
                    key={t}
                    onClick={() => setNewType(t)}
                    className="flex-1 text-[11px] py-1.5 rounded-md border transition-colors font-medium"
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

            <div className="flex flex-col gap-2">
              <input
                value={newName}
                onChange={e => { setNewName(e.target.value); setCreateError(""); }}
                placeholder="Nom de la page…"
                onKeyDown={e => { if (e.key === "Enter") createPage(); }}
                autoFocus
                className="w-full bg-[#0d1117] border border-[#212226] rounded-md px-3 py-2 text-[13px] text-[#ececed] placeholder:text-[#6b6c72] outline-none focus:border-[#2a2b31] transition-colors"
              />
              {newName && (
                <p className="text-[11px] text-[#6b6c72] font-mono pl-1">{autoSlug(newName)}</p>
              )}
              <input
                value={newTags}
                onChange={e => setNewTags(e.target.value)}
                placeholder="Tags (séparés par virgule)…"
                className="w-full bg-[#0d1117] border border-[#212226] rounded-md px-3 py-2 text-[13px] text-[#ececed] placeholder:text-[#6b6c72] outline-none focus:border-[#2a2b31] transition-colors"
              />
            </div>

            {createError && <p className="text-[12px] text-red-400">{createError}</p>}

            <div className="flex gap-2">
              <button
                onClick={createPage}
                disabled={creating || !newName.trim()}
                className="flex-1 flex items-center justify-center gap-1.5 text-[13px] py-2 text-white rounded-md disabled:opacity-50 hover:brightness-110 transition-all"
                style={{ background: ACCENT }}
              >
                {creating ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                Créer
              </button>
              <button
                onClick={() => { setShowNewForm(false); setCreateError(""); setNewName(""); setNewTags(""); }}
                className="text-[13px] px-4 py-2 text-[#a1a1a6] hover:text-[#ececed] border border-[#212226] hover:border-[#2a2b31] rounded-md transition-colors"
              >
                Annuler
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
