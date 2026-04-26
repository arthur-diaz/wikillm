import React, { useEffect, useRef, useState } from "react";
import { CheckCircle, AlertCircle, Loader, Plus, Trash2, Wifi } from "lucide-react";
import { useCorpus } from "../CorpusContext";
import { Card, SectionTitle, Slider, BtnPrimary, BtnSecondary, TextInput, ACCENT } from "./ui/design";

type ModelFile = { filename: string; path: string; size_gb: number; active: boolean };
type Settings = { model: { path: string; n_ctx: number; n_gpu_layers: number }; inference: { temperature: number; max_tokens: number; top_p: number } };
type ModelStatus = { status: "loaded" | "loading" | "error"; error: string | null };
type LintResult = {
  pages: number; total_tokens: number;
  broken_links: { page: string; target: string }[];
  invalid_frontmatter: { page: string; reason: string }[];
  orphans: string[]; in_wiki_not_index: string[]; in_index_not_wiki: string[];
  date_inconsistencies: { slug: string; last_updated: string; last_touched: string }[];
};
type StatsData = { raw_files: number; pages: { total: number; sources: number; entities: number; concepts: number; analyses: number }; size: { tokens: number }; budget: number; log_entries: number; last_log: string | null };

type MissingConcept = { name: string; slug: string; count: number; pages: string[] };
type Contradiction = { page_a: string; page_b: string; explanation: string };
type SourceGap = { description: string; source_type: string; keywords: string };
type StaleClaim = { slug: string; explanation: string; suggestion: string };
type DateIssue = { slug: string; last_updated: string; last_touched: string };
type SemanticResult = {
  estimate: number | null;
  step: string | null;
  date_inconsistencies: DateIssue[];
  missing_concepts: MissingConcept[];
  contradictions: Contradiction[];
  source_gaps: SourceGap[];
  stale_claims: StaleClaim[];
  done: boolean;
  error: string | null;
};

type ComparePages = { slug_a: string; slug_b: string };
type PageContent = { slug: string; content: string; meta: Record<string, unknown> };

const PROVIDERS = {
  openai:    { label: "OpenAI",    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"] },
  anthropic: { label: "Anthropic", models: ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"] },
  mistral:   { label: "Mistral",   models: ["mistral-large-latest", "mistral-small-latest", "open-mixtral-8x22b"] },
} as const;
type ProviderKey = keyof typeof PROVIDERS;

function LLMProviderSection({ mode, setMode }: { mode: "local" | "api"; setMode: (m: "local" | "api") => void }) {
  // API state
  const [provider, setProvider] = useState<ProviderKey>("openai");
  const [apiModel, setApiModel] = useState<string>("gpt-4o");
  const [apiKey, setApiKey] = useState("");
  const [hasKey, setHasKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);

  // Local state
  const [ggufModels, setGgufModels] = useState<ModelFile[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState<ModelStatus>({ status: "loaded", error: null });
  const [selectedModel, setSelectedModel] = useState("");
  const [nCtx, setNCtx] = useState(8192);
  const [nGpuLayers, setNGpuLayers] = useState(-1);
  const [temperature, setTemperature] = useState(0.3);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [topP, setTopP] = useState(0.9);

  // Shared
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    Promise.all([
      fetch("/api/settings").then(r => r.json()),
      fetch("/api/settings/models").then(r => r.json()),
      fetch("/api/settings/status").then(r => r.json()),
    ]).then(([s, m, st]) => {
      // Provider
      const p = s.provider ?? {};
      const newMode: "local" | "api" = p.mode === "api" ? "api" : "local";
      const pv: ProviderKey = (p.api_provider in PROVIDERS ? p.api_provider : "openai") as ProviderKey;
      setMode(newMode);
      setProvider(pv);
      setApiModel(p.api_model || PROVIDERS[pv].models[0]);
      setHasKey(!!p.has_key);
      // Local model
      setSettings(s); setGgufModels(m); setStatus(st);
      setSelectedModel(s.model.path); setNCtx(s.model.n_ctx);
      setNGpuLayers(s.model.n_gpu_layers); setTemperature(s.inference.temperature);
      setMaxTokens(s.inference.max_tokens); setTopP(s.inference.top_p);
    });
  }, []);

  useEffect(() => {
    if (status.status !== "loading") return;
    const id = setInterval(async () => {
      const st = await fetch("/api/settings/status").then(r => r.json());
      setStatus(st);
      if (st.status !== "loading") clearInterval(id);
    }, 2000);
    return () => clearInterval(id);
  }, [status.status]);

  const changeProvider = (pv: ProviderKey) => {
    setProvider(pv); setApiModel(PROVIDERS[pv].models[0]);
    setHasKey(false); setApiKey(""); setTestResult(null);
  };

  const testConnection = async () => {
    setTesting(true); setTestResult(null);
    try {
      const r = await fetch("/api/settings/test-connection", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, api_key: apiKey || undefined, model: apiModel }),
      });
      const d = await r.json();
      setTestResult(r.ok ? { ok: true, msg: d.message } : { ok: false, msg: d.detail || "Erreur" });
    } catch { setTestResult({ ok: false, msg: "Erreur réseau" }); }
    setTesting(false);
  };

  const apply = async () => {
    setSaving(true);
    if (mode === "local") {
      await fetch("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_path: selectedModel !== settings?.model.path ? selectedModel : undefined,
          n_ctx: nCtx, n_gpu_layers: nGpuLayers, temperature, max_tokens: maxTokens, top_p: topP,
        }),
      });
      const st = await fetch("/api/settings/status").then(r => r.json());
      setStatus(st);
    } else {
      const r = await fetch("/api/settings/llm-provider", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, api_provider: provider, api_model: apiModel, api_key: apiKey || undefined }),
      });
      if (r.ok && apiKey) { setApiKey(""); setHasKey(true); }
    }
    setSaving(false); setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const tabCls = (active: boolean) =>
    `flex-1 py-1.5 rounded-md text-[12px] border transition-colors ${active ? "border-[#8b5cf6]/50 bg-[#8b5cf6]/8 text-[#c4b5fd]" : "border-[#212226] text-[#6b6c72] hover:text-[#ececed] hover:border-[#2a2b31]"}`;

  return (
    <>
      <SectionTitle>Fournisseur LLM</SectionTitle>

      {/* Mode toggle */}
      <div className="flex gap-2 mb-3">
        {(["local", "api"] as const).map(m => (
          <button key={m} onClick={() => { setMode(m); setSaved(false); }} className={tabCls(mode === m)}>
            {m === "local" ? "Local (GGUF)" : "API externe"}
          </button>
        ))}
      </div>

      {mode === "local" && (
        <>
          <Card>
            <div className="flex justify-between items-center mb-3">
              <span className="text-[11px] text-[#6b6c72] uppercase tracking-wide">Statut</span>
              {status.status === "loading" && <span className="flex items-center gap-1.5 text-[11px] text-yellow-400"><Loader size={11} className="animate-spin" /> Chargement…</span>}
              {status.status === "error"   && <span className="flex items-center gap-1.5 text-[11px] text-red-400"><AlertCircle size={11} /> Erreur</span>}
              {status.status === "loaded"  && <span className="flex items-center gap-1.5 text-[11px] text-[#34d399]"><CheckCircle size={11} /> Prêt</span>}
            </div>
            {ggufModels.length === 0 ? (
              <p className="text-[11px] text-[#6b6c72]">Aucun fichier .gguf dans models/</p>
            ) : ggufModels.map(m => (
              <label key={m.path} className={`flex items-center justify-between rounded-md px-3 py-2 mb-1.5 cursor-pointer border transition-colors ${selectedModel === m.path ? "border-[#8b5cf6]/50 bg-[#8b5cf6]/8" : "border-[#212226] hover:border-[#2a2b31] hover:bg-[#1d1e22]"}`}>
                <div className="flex items-center gap-2">
                  <input type="radio" name="model" value={m.path} checked={selectedModel === m.path} onChange={() => setSelectedModel(m.path)} style={{ accentColor: ACCENT }} />
                  <span className="text-[13px] text-[#ececed]">{m.filename}</span>
                </div>
                <span className="text-[11px] text-[#6b6c72]">{m.size_gb} GB</span>
              </label>
            ))}
          </Card>
          <Card>
            <p className="text-[11px] text-[#6b6c72] uppercase tracking-wide mb-3">Contexte & GPU</p>
            <div className="space-y-1">
              <Slider label="n_ctx" value={nCtx} min={2048} max={32768} step={512} onChange={setNCtx} />
              <Slider label="n_gpu_layers  (-1 = tout)" value={nGpuLayers} min={-1} max={50} step={1} onChange={setNGpuLayers} />
            </div>
          </Card>
          <Card>
            <p className="text-[11px] text-[#6b6c72] uppercase tracking-wide mb-3">Inférence</p>
            <div className="space-y-1">
              <Slider label="Temperature" value={temperature} min={0} max={1} step={0.05} onChange={setTemperature} format={v => v.toFixed(2)} />
              <Slider label="max_tokens" value={maxTokens} min={256} max={4096} step={128} onChange={setMaxTokens} />
              <Slider label="top_p" value={topP} min={0.5} max={1} step={0.05} onChange={setTopP} format={v => v.toFixed(2)} />
            </div>
          </Card>
          {status.status === "error" && status.error && (
            <p className="text-[11px] text-red-400 bg-red-950/20 border border-red-900 rounded-md p-3 mb-4">{status.error}</p>
          )}
        </>
      )}

      {mode === "api" && (
        <Card>
          <p className="text-[11px] text-[#6b6c72] uppercase tracking-wide mb-2">Fournisseur</p>
          <div className="flex gap-2 mb-4">
            {(Object.entries(PROVIDERS) as [ProviderKey, { label: string; models: readonly string[] }][]).map(([key, { label }]) => (
              <button key={key} onClick={() => changeProvider(key)} className={tabCls(provider === key)}>{label}</button>
            ))}
          </div>

          <p className="text-[11px] text-[#6b6c72] uppercase tracking-wide mb-2">Modèle</p>
          <div className="space-y-1 mb-4">
            {PROVIDERS[provider].models.map(m => (
              <label key={m} className={`flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer border transition-colors ${apiModel === m ? "border-[#8b5cf6]/50 bg-[#8b5cf6]/8" : "border-[#212226] hover:border-[#2a2b31] hover:bg-[#1d1e22]"}`}>
                <input type="radio" name="api-model" value={m} checked={apiModel === m} onChange={() => setApiModel(m)} style={{ accentColor: ACCENT }} />
                <span className="text-[13px] text-[#ececed]">{m}</span>
              </label>
            ))}
          </div>

          <p className="text-[11px] text-[#6b6c72] uppercase tracking-wide mb-2">Clé API</p>
          <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
            placeholder={hasKey ? "••••••••••••••••••• (clé existante)" : "Entrez votre clé API…"}
            className="w-full bg-[#111113] border border-[#212226] rounded-md px-3 py-2 text-[12px] text-[#ececed] placeholder:text-[#3a3b41] mb-3 focus:outline-none focus:border-[#8b5cf6]/50"
          />
          <button onClick={testConnection} disabled={testing || (!apiKey && !hasKey)}
            className="flex items-center justify-center gap-1.5 w-full mb-2 py-2 rounded-md text-[12px] border border-[#212226] text-[#a1a1a6] hover:text-[#ececed] hover:border-[#2a2b31] disabled:opacity-40 transition-colors">
            {testing ? <><Loader size={11} className="animate-spin" /> Test en cours…</> : <><Wifi size={11} /> Tester la connexion</>}
          </button>
          {testResult && (
            <p className={`text-[11px] rounded-md px-3 py-2 mb-1 ${testResult.ok ? "bg-green-950/20 border border-green-900 text-[#34d399]" : "bg-red-950/20 border border-red-900 text-red-400"}`}>
              {testResult.ok ? "✓ " : "✗ "}{testResult.msg}
            </p>
          )}
        </Card>
      )}

      <BtnPrimary onClick={apply} disabled={saving || (mode === "local" && status.status === "loading")} className="w-full mb-6">
        {saving ? "Enregistrement…" : saved ? "Enregistré ✓" : "Appliquer"}
      </BtnPrimary>
    </>
  );
}

function CorpusSection({ onCorpusChange }: { onCorpusChange: () => void }) {
  const { active, list, refresh } = useCorpus();
  const [nameInput, setNameInput] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteInput, setDeleteInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (creating) inputRef.current?.focus(); }, [creating]);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3000); };

  const create = async () => {
    if (!nameInput.trim()) return;
    setBusy(true);
    const r = await fetch("/api/corpus", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: nameInput.trim() }) });
    setBusy(false);
    if (r.ok) { const d = await r.json(); setNameInput(""); setCreating(false); await refresh(); onCorpusChange(); flash(`Corpus « ${d.name} » créé.`); }
    else { const e = await r.json(); flash(e.detail || "Erreur"); }
  };

  const del = async (id: string) => {
    setBusy(true);
    const r = await fetch(`/api/corpus/${id}`, { method: "DELETE" });
    setBusy(false);
    if (r.ok) { setDeleteTarget(null); setDeleteInput(""); await refresh(); onCorpusChange(); flash("Corpus supprimé."); }
    else { const e = await r.json(); flash(e.detail || "Erreur"); }
  };

  return (
    <>
      <SectionTitle>Corpus</SectionTitle>
      {msg && <p className="text-[11px] text-[#34d399] mb-3">{msg}</p>}

      <Card>
        <div className="space-y-2">
          {list.map(c => (
            <div key={c.id}>
              <div className={`flex items-center justify-between rounded-md px-3 py-2 border transition-colors ${c.id === active ? "border-[#8b5cf6]/50 bg-[#8b5cf6]/8" : "border-[#212226]"}`}>
                <span className={`text-[13px] ${c.id === active ? "text-[#c4b5fd]" : "text-[#ececed]"}`}>
                  {c.name}{c.id === active && <span className="ml-2 text-[10.5px] text-[#a78bfa]">actif</span>}
                </span>
                {c.id !== "default" && c.id !== active && (
                  <button onClick={() => { setDeleteTarget(c.id); setDeleteInput(""); }}
                    className="text-[#6b6c72] hover:text-red-400 transition-colors p-1">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>

              {deleteTarget === c.id && (
                <div className="mt-1.5 rounded-md border border-red-900/50 bg-red-950/10 p-3">
                  <p className="text-[11px] text-red-400 mb-2">Tapez <span className="font-mono font-bold">"{c.name}"</span> pour confirmer :</p>
                  <div className="flex gap-2">
                    <TextInput
                      value={deleteInput}
                      onChange={e => setDeleteInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Escape") { setDeleteTarget(null); setDeleteInput(""); } }}
                      autoFocus
                    />
                    <button onClick={() => del(c.id)} disabled={busy || deleteInput !== c.name}
                      className="text-[12px] bg-red-700 hover:bg-red-600 disabled:opacity-40 text-white px-3 py-1 rounded-md">
                      Supprimer
                    </button>
                    <button onClick={() => { setDeleteTarget(null); setDeleteInput(""); }}
                      className="text-[12px] text-[#6b6c72] hover:text-[#ececed] px-2">
                      ✕
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mt-3 pt-3 border-t border-[#212226]">
          {creating ? (
            <div className="flex gap-2">
              <TextInput ref={inputRef as React.Ref<HTMLInputElement>} value={nameInput} onChange={e => setNameInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(false); }}
                placeholder="Nom du corpus" />
              <BtnPrimary onClick={create} disabled={busy || !nameInput.trim()}>{busy ? "…" : "Créer"}</BtnPrimary>
              <button onClick={() => { setCreating(false); setNameInput(""); }} className="text-[12px] text-[#6b6c72] hover:text-[#ececed] px-2">✕</button>
            </div>
          ) : (
            <button onClick={() => setCreating(true)}
              className="flex items-center gap-1.5 text-[12px] text-[#a1a1a6] hover:text-[#ececed] transition-colors">
              <Plus size={12} /> Nouveau corpus
            </button>
          )}
        </div>
      </Card>
    </>
  );
}

// ── Modal côte à côte ────────────────────────────────────────────────────

function CompareModal({ slugs, onClose }: { slugs: ComparePages; onClose: () => void }) {
  const [pages, setPages] = useState<[PageContent | null, PageContent | null]>([null, null]);

  useEffect(() => {
    Promise.all([
      fetch(`/api/pages/${slugs.slug_a}`).then(r => r.json()),
      fetch(`/api/pages/${slugs.slug_b}`).then(r => r.json()),
    ]).then(([a, b]) => setPages([a, b]));
  }, [slugs]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70" onClick={onClose}>
      <div className="relative bg-[#111113] border border-[#212226] rounded-xl w-[90vw] max-w-5xl max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#212226]">
          <span className="text-[13px] text-[#ececed] font-semibold">
            [[{slugs.slug_a}]] ↔ [[{slugs.slug_b}]]
          </span>
          <button onClick={onClose} className="text-[#6b6c72] hover:text-[#ececed] text-lg leading-none">✕</button>
        </div>
        <div className="flex flex-1 overflow-hidden divide-x divide-[#212226]">
          {([pages[0], pages[1]] as (PageContent | null)[]).map((page, i) => (
            <div key={i} className="flex-1 overflow-y-auto p-4 ui-scroll">
              {page ? (
                <>
                  <p className="text-[11px] text-[#a78bfa] font-semibold mb-2">[[{page.slug}]]</p>
                  <pre className="text-[11px] text-[#a1a1a6] whitespace-pre-wrap font-mono">{page.content}</pre>
                </>
              ) : (
                <p className="text-[11px] text-[#6b6c72]">Chargement…</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Pill d'action inline ─────────────────────────────────────────────────

function ActionPill({ label, onClick, color = "default" }: { label: string; onClick: () => void; color?: "default" | "purple" | "red" }) {
  const cls = {
    default: "border-[#212226] text-[#a1a1a6] hover:border-[#2a2b31] hover:text-[#ececed]",
    purple:  "border-[#8b5cf6]/40 text-[#c4b5fd] hover:border-[#8b5cf6]/70",
    red:     "border-red-900/40 text-red-400 hover:border-red-700",
  }[color];
  return (
    <button onClick={onClick}
      className={`text-[10px] px-2 py-0.5 rounded border transition-colors ${cls}`}>
      {label}
    </button>
  );
}

// ── Section Santé ─────────────────────────────────────────────────────────

function HealthSection() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [lint, setLint] = useState<LintResult | null>(null);
  const [linting, setLinting] = useState(false);
  const [semanticEnabled, setSemanticEnabled] = useState(false);
  const [semantic, setSemantic] = useState<SemanticResult>({
    estimate: null, step: null,
    date_inconsistencies: [], missing_concepts: [],
    contradictions: [], source_gaps: [], stale_claims: [],
    done: false, error: null,
  });
  const [semanticRunning, setSemanticRunning] = useState(false);
  const [compareModal, setCompareModal] = useState<ComparePages | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => { fetch("/api/stats").then(r => r.json()).then(setStats); }, []);

  const runLint = async (autoFix = false) => {
    setLinting(true);
    setLint(await fetch(`/api/lint?auto_fix=${autoFix}`).then(r => r.json()));
    setLinting(false);
  };

  const runSemantic = () => {
    if (esRef.current) { esRef.current.close(); }
    setSemantic({ estimate: null, step: null, date_inconsistencies: [], missing_concepts: [], contradictions: [], source_gaps: [], stale_claims: [], done: false, error: null });
    setSemanticRunning(true);

    const es = new EventSource("/api/lint/semantic");
    esRef.current = es;

    es.addEventListener("estimate", e => {
      const d = JSON.parse(e.data);
      setSemantic(prev => ({ ...prev, estimate: d.llm_calls }));
    });
    es.addEventListener("progress", e => {
      const d = JSON.parse(e.data);
      setSemantic(prev => ({ ...prev, step: d.msg }));
    });
    es.addEventListener("date_inconsistencies", e => {
      setSemantic(prev => ({ ...prev, date_inconsistencies: JSON.parse(e.data) }));
    });
    es.addEventListener("missing_concepts", e => {
      setSemantic(prev => ({ ...prev, missing_concepts: JSON.parse(e.data) }));
    });
    es.addEventListener("contradictions", e => {
      setSemantic(prev => ({ ...prev, contradictions: JSON.parse(e.data) }));
    });
    es.addEventListener("source_gaps", e => {
      setSemantic(prev => ({ ...prev, source_gaps: JSON.parse(e.data) }));
    });
    es.addEventListener("stale_claims", e => {
      setSemantic(prev => ({ ...prev, stale_claims: JSON.parse(e.data) }));
    });
    es.addEventListener("done", () => {
      setSemantic(prev => ({ ...prev, done: true, step: null }));
      setSemanticRunning(false);
      es.close();
    });
    es.onerror = () => {
      setSemantic(prev => ({ ...prev, error: "Erreur de connexion SSE", step: null }));
      setSemanticRunning(false);
      es.close();
    };
  };

  useEffect(() => () => { esRef.current?.close(); }, []);

  const dismiss = (key: string) => setDismissed(prev => new Set([...prev, key]));

  const createStub = async (name: string, slug: string) => {
    const r = await fetch("/api/pages", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "concept", name }),
    });
    if (r.ok) setSemantic(prev => ({ ...prev, missing_concepts: prev.missing_concepts.filter(c => c.slug !== slug) }));
  };

  const structuralIssues = lint
    ? lint.broken_links.length + lint.invalid_frontmatter.length + lint.orphans.length
      + lint.in_wiki_not_index.length + lint.in_index_not_wiki.length
      + (lint.date_inconsistencies?.length ?? 0)
    : 0;

  const semanticIssues = semantic.missing_concepts.length + semantic.contradictions.length
    + semantic.stale_claims.length + semantic.date_inconsistencies.length;

  const pct = stats ? Math.round((stats.size.tokens / stats.budget) * 100) : 0;

  return (
    <>
      <SectionTitle>Santé du wiki</SectionTitle>

      {stats && (
        <Card>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[
              ["Pages", stats.pages.total],
              ["Sources brutes", stats.raw_files],
              ["Entrées log", stats.log_entries],
            ].map(([l, v]) => (
              <div key={l as string} className="text-center">
                <div className="text-[17px] font-semibold text-[#ececed]">{v}</div>
                <div className="text-[11px] text-[#6b6c72] mt-0.5">{l}</div>
              </div>
            ))}
          </div>
          <div>
            <div className="flex justify-between text-[11px] mb-1">
              <span className="text-[#6b6c72]">Tokens wiki / budget</span>
              <span className={pct > 100 ? "text-yellow-400" : "text-[#a1a1a6]"}>{pct}%</span>
            </div>
            <div className="w-full bg-[#212226] rounded-full h-1">
              <div className="h-1 rounded-full" style={{ width: `${Math.min(pct, 100)}%`, background: pct > 100 ? "#eab308" : ACCENT }} />
            </div>
          </div>
          {stats.last_log && <p className="text-[11px] text-[#6b6c72] mt-2 truncate">{stats.last_log}</p>}
        </Card>
      )}

      {/* Toggle lint sémantique */}
      <label className="flex items-center justify-between mb-3 cursor-pointer">
        <span className="text-[12px] text-[#a1a1a6]">Lint sémantique (utilise le LLM)</span>
        <div
          onClick={() => setSemanticEnabled(v => !v)}
          className={`relative w-8 h-4.5 rounded-full transition-colors ${semanticEnabled ? "bg-[#8b5cf6]" : "bg-[#212226]"}`}
          style={{ height: "18px", minWidth: "32px" }}
        >
          <div className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-transform ${semanticEnabled ? "translate-x-4" : "translate-x-0.5"}`} />
        </div>
      </label>

      {/* Boutons lint */}
      <div className="flex gap-2 mb-3">
        <BtnSecondary onClick={() => runLint(false)} disabled={linting} className="flex-1">
          {linting ? "Analyse…" : "Lancer lint"}
        </BtnSecondary>
        {lint && <BtnPrimary onClick={() => runLint(true)} disabled={linting} className="text-[11px] px-3">Auto-fix</BtnPrimary>}
        {semanticEnabled && (
          <BtnSecondary onClick={runSemantic} disabled={semanticRunning} className="flex-1">
            {semanticRunning
              ? (semantic.step ? <span className="truncate text-[10px]">{semantic.step}</span> : "LLM…")
              : "Lint sémantique"}
          </BtnSecondary>
        )}
      </div>

      {/* Rapport structurel */}
      {lint && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <span className="text-[13px] text-[#ececed]">{lint.pages} pages · {lint.total_tokens.toLocaleString()} tokens</span>
            <span className={`text-[13px] font-semibold ${structuralIssues === 0 ? "text-[#34d399]" : "text-red-400"}`}>
              {structuralIssues === 0 ? "✓ OK" : `${structuralIssues} problème${structuralIssues > 1 ? "s" : ""}`}
            </span>
          </div>

          {[
            ["Liens cassés", lint.broken_links.map(b => `${b.page} → [[${b.target}]]`), "text-red-400"],
            ["Frontmatter invalide", lint.invalid_frontmatter.map(f => `${f.page}: ${f.reason}`), "text-yellow-400"],
            ["Pages orphelines", lint.orphans.map(s => `[[${s}]]`), "text-orange-400"],
            ["Absentes de l'index", lint.in_wiki_not_index.map(s => `[[${s}]]`), "text-[#a78bfa]"],
            ["Fantômes dans l'index", lint.in_index_not_wiki.map(s => `[[${s}]]`), "text-[#6b6c72]"],
          ].map(([title, items, color]) => (items as string[]).length > 0 && (
            <div key={title as string} className="mt-3">
              <p className={`text-[11px] font-semibold mb-1 ${color}`}>{title as string} ({(items as string[]).length})</p>
              <ul className="space-y-0.5">
                {(items as string[]).map((item, i) => (
                  <li key={i} className="text-[11px] text-[#a1a1a6] font-mono bg-[#111113] border border-[#212226] rounded px-2 py-1 truncate">{item}</li>
                ))}
              </ul>
            </div>
          ))}

          {/* Dates incohérentes */}
          {(lint.date_inconsistencies?.length ?? 0) > 0 && (
            <div className="mt-3">
              <p className="text-[11px] font-semibold mb-1 text-yellow-400">
                Dates incohérentes ({lint.date_inconsistencies.length})
              </p>
              <ul className="space-y-0.5">
                {lint.date_inconsistencies.map((d, i) => (
                  <li key={i} className="text-[11px] text-[#a1a1a6] font-mono bg-[#111113] border border-[#212226] rounded px-2 py-1">
                    [[{d.slug}]] — last_updated {d.last_updated} &lt; ingest {d.last_touched}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}

      {/* Rapport sémantique */}
      {semanticEnabled && (semantic.estimate !== null || semantic.done) && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <span className="text-[13px] text-[#ececed]">Lint sémantique</span>
            <div className="flex items-center gap-2">
              {semantic.estimate !== null && (
                <span className="text-[10px] text-[#6b6c72]">~{semantic.estimate} appels LLM</span>
              )}
              {semantic.done && (
                <span className={`text-[13px] font-semibold ${semanticIssues === 0 ? "text-[#34d399]" : "text-yellow-400"}`}>
                  {semanticIssues === 0 ? "✓ OK" : `${semanticIssues} signal${semanticIssues > 1 ? "s" : ""}`}
                </span>
              )}
              {semanticRunning && <Loader size={11} className="animate-spin text-[#a78bfa]" />}
            </div>
          </div>

          {semantic.error && (
            <p className="text-[11px] text-red-400 bg-red-950/20 border border-red-900 rounded px-2 py-1 mb-2">{semantic.error}</p>
          )}

          {/* Dates incohérentes (sémantique — peut apparaître avant le lint structurel) */}
          {semantic.date_inconsistencies.length > 0 && (
            <SemanticSection title="Dates incohérentes" count={semantic.date_inconsistencies.length} color="text-yellow-400">
              {semantic.date_inconsistencies
                .filter(d => !dismissed.has(`date:${d.slug}`))
                .map(d => (
                  <SemanticItem key={d.slug} onDismiss={() => dismiss(`date:${d.slug}`)}>
                    <span className="font-mono text-[11px]">[[{d.slug}]]</span>
                    <span className="text-[#6b6c72] ml-1">— {d.last_updated} &lt; {d.last_touched}</span>
                  </SemanticItem>
                ))}
            </SemanticSection>
          )}

          {/* Concepts manquants */}
          {semantic.missing_concepts.length > 0 && (
            <SemanticSection title="Concepts sans page propre" count={semantic.missing_concepts.length} color="text-[#a78bfa]">
              {semantic.missing_concepts
                .filter(c => !dismissed.has(`mc:${c.slug}`))
                .map(c => (
                  <SemanticItem key={c.slug} onDismiss={() => dismiss(`mc:${c.slug}`)}>
                    <div className="flex items-start justify-between gap-2 w-full">
                      <div className="flex-1 min-w-0">
                        <span className="font-semibold text-[11px] text-[#c4b5fd]">{c.name}</span>
                        <span className="text-[#6b6c72] text-[10px] ml-1">({c.count}x dans {c.pages.length} page{c.pages.length > 1 ? "s" : ""})</span>
                        <div className="text-[10px] text-[#6b6c72] truncate">{c.pages.map(p => `[[${p}]]`).join(" · ")}</div>
                      </div>
                      <ActionPill label="Créer stub" color="purple" onClick={() => createStub(c.name, c.slug)} />
                    </div>
                  </SemanticItem>
                ))}
            </SemanticSection>
          )}

          {/* Contradictions */}
          {semantic.contradictions.length > 0 && (
            <SemanticSection title="Contradictions inter-pages" count={semantic.contradictions.length} color="text-red-400">
              {semantic.contradictions
                .filter(c => !dismissed.has(`ctr:${c.page_a}:${c.page_b}`))
                .map(c => (
                  <SemanticItem key={`${c.page_a}:${c.page_b}`} onDismiss={() => dismiss(`ctr:${c.page_a}:${c.page_b}`)}>
                    <div className="flex items-start justify-between gap-2 w-full">
                      <div className="flex-1 min-w-0">
                        <span className="font-mono text-[11px] text-red-300">[[{c.page_a}]] ↔ [[{c.page_b}]]</span>
                        <div className="text-[10px] text-[#a1a1a6] mt-0.5">{c.explanation}</div>
                      </div>
                      <ActionPill
                        label="Côte à côte"
                        onClick={() => setCompareModal({ slug_a: c.page_a, slug_b: c.page_b })}
                      />
                    </div>
                  </SemanticItem>
                ))}
            </SemanticSection>
          )}

          {/* Lacunes de sources */}
          {semantic.source_gaps.length > 0 && (
            <SemanticSection title="Lacunes thématiques" count={semantic.source_gaps.length} color="text-orange-400">
              {semantic.source_gaps
                .filter((_, i) => !dismissed.has(`gap:${i}`))
                .map((g, i) => (
                  <SemanticItem key={i} onDismiss={() => dismiss(`gap:${i}`)}>
                    <div>
                      <div className="text-[11px] text-[#ececed]">{g.description}</div>
                      <div className="text-[10px] text-[#6b6c72] mt-0.5">
                        {g.source_type} · <span className="font-mono">{g.keywords}</span>
                      </div>
                    </div>
                  </SemanticItem>
                ))}
            </SemanticSection>
          )}

          {/* Claims obsolètes */}
          {semantic.stale_claims.length > 0 && (
            <SemanticSection title="Affirmations possiblement obsolètes" count={semantic.stale_claims.length} color="text-yellow-400">
              {semantic.stale_claims
                .filter(s => !dismissed.has(`stale:${s.slug}`))
                .map(s => (
                  <SemanticItem key={s.slug} onDismiss={() => dismiss(`stale:${s.slug}`)}>
                    <div className="flex items-start justify-between gap-2 w-full">
                      <div className="flex-1 min-w-0">
                        <span className="font-mono text-[11px] text-[#c4b5fd]">[[{s.slug}]]</span>
                        <div className="text-[10px] text-[#a1a1a6] mt-0.5">{s.explanation}</div>
                        <div className="text-[10px] text-[#6b6c72] mt-0.5 italic">{s.suggestion}</div>
                      </div>
                    </div>
                  </SemanticItem>
                ))}
            </SemanticSection>
          )}

          {semantic.done && semanticIssues === 0 && (
            <p className="text-[11px] text-[#34d399] mt-2">Aucun signal sémantique détecté.</p>
          )}
        </Card>
      )}

      {compareModal && (
        <CompareModal slugs={compareModal} onClose={() => setCompareModal(null)} />
      )}
    </>
  );
}

// ── Composants helper pour l'affichage sémantique ────────────────────────

function SemanticSection({ title, count, color, children }: { title: string; count: number; color: string; children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <p className={`text-[11px] font-semibold mb-1.5 ${color}`}>{title} ({count})</p>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function SemanticItem({ children, onDismiss }: { children: React.ReactNode; onDismiss: () => void }) {
  return (
    <div className="flex items-start gap-2 bg-[#111113] border border-[#212226] rounded-md px-2.5 py-2">
      <div className="flex-1 min-w-0">{children}</div>
      <button onClick={onDismiss} className="text-[#3a3b41] hover:text-[#6b6c72] text-[12px] leading-none mt-0.5 shrink-0">✕</button>
    </div>
  );
}

export default function SettingsPanel({ onCorpusChange }: { onCorpusChange: () => void }) {
  const [mode, setMode] = useState<"local" | "api">("local");

  return (
    <div className="h-full overflow-y-auto p-6 ui-scroll" style={{ background: "#0a0a0b" }}>
      <div className="max-w-xl mx-auto">
        <h2 className="text-[15px] font-semibold text-[#ececed] mb-5">Réglages</h2>
        <LLMProviderSection mode={mode} setMode={setMode} />
        <hr className="border-[#212226] my-5" />
        <CorpusSection onCorpusChange={onCorpusChange} />
        <hr className="border-[#212226] my-5" />
        <HealthSection />
      </div>
    </div>
  );
}
