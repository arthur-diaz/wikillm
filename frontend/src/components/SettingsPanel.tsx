import { useEffect, useRef, useState } from "react";
import { RefreshCw, CheckCircle, AlertCircle, Loader, Plus, Trash2 } from "lucide-react";
import { useCorpus } from "../CorpusContext";

// ─── Types ────────────────────────────────────────────────────────────────────

type ModelFile = { filename: string; path: string; size_gb: number; active: boolean };
type Settings = { model: { path: string; n_ctx: number; n_gpu_layers: number }; inference: { temperature: number; max_tokens: number; top_p: number } };
type ModelStatus = { status: "loaded" | "loading" | "error"; error: string | null };
type LintResult = { pages: number; total_tokens: number; broken_links: { page: string; target: string }[]; invalid_frontmatter: { page: string; reason: string }[]; orphans: string[]; in_wiki_not_index: string[]; in_index_not_wiki: string[] };
type StatsData = { raw_files: number; pages: { total: number; sources: number; entities: number; concepts: number }; size: { tokens: number }; budget: number; log_entries: number; last_log: string | null };

// ─── Sub-components ───────────────────────────────────────────────────────────

function Slider({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void }) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="text-gray-200 font-mono">{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} className="w-full accent-blue-500" />
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">{children}</h3>;
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-gray-800 p-4 mb-4" style={{ background: "#161b22" }}>{children}</div>;
}

// ─── Modèle section ───────────────────────────────────────────────────────────

function ModelSection() {
  const [models, setModels] = useState<ModelFile[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState<ModelStatus>({ status: "loaded", error: null });
  const [selectedModel, setSelectedModel] = useState("");
  const [nCtx, setNCtx] = useState(8192);
  const [nGpuLayers, setNGpuLayers] = useState(-1);
  const [temperature, setTemperature] = useState(0.3);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [topP, setTopP] = useState(0.9);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = async () => {
    const [s, m, st] = await Promise.all([
      fetch("/api/settings").then(r => r.json()),
      fetch("/api/settings/models").then(r => r.json()),
      fetch("/api/settings/status").then(r => r.json()),
    ]);
    setSettings(s); setModels(m); setStatus(st);
    setSelectedModel(s.model.path); setNCtx(s.model.n_ctx);
    setNGpuLayers(s.model.n_gpu_layers); setTemperature(s.inference.temperature);
    setMaxTokens(s.inference.max_tokens); setTopP(s.inference.top_p);
  };

  useEffect(() => { load(); }, []);

  useEffect(() => {
    if (status.status !== "loading") return;
    const id = setInterval(async () => {
      const st = await fetch("/api/settings/status").then(r => r.json());
      setStatus(st);
      if (st.status !== "loading") clearInterval(id);
    }, 2000);
    return () => clearInterval(id);
  }, [status.status]);

  const apply = async () => {
    setSaving(true);
    await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_path: selectedModel !== settings?.model.path ? selectedModel : undefined,
        n_ctx: nCtx, n_gpu_layers: nGpuLayers, temperature, max_tokens: maxTokens, top_p: topP,
      }),
    });
    setSaving(false); setSaved(true);
    setTimeout(() => setSaved(false), 2500);
    const st = await fetch("/api/settings/status").then(r => r.json());
    setStatus(st);
  };

  return (
    <>
      <SectionTitle>Modèle</SectionTitle>

      <Card>
        <div className="flex justify-between items-center mb-3">
          <span className="text-xs text-gray-500">Statut</span>
          {status.status === "loading" && <span className="flex items-center gap-1.5 text-xs text-yellow-400"><Loader size={11} className="animate-spin" /> Chargement…</span>}
          {status.status === "error"   && <span className="flex items-center gap-1.5 text-xs text-red-400"><AlertCircle size={11} /> Erreur</span>}
          {status.status === "loaded"  && <span className="flex items-center gap-1.5 text-xs text-green-400"><CheckCircle size={11} /> Prêt</span>}
        </div>
        {models.length === 0 ? (
          <p className="text-xs text-gray-600">Aucun fichier .gguf dans models/</p>
        ) : models.map(m => (
          <label key={m.path} className={`flex items-center justify-between rounded px-3 py-2 mb-1.5 cursor-pointer border ${selectedModel === m.path ? "border-blue-600 bg-blue-950/20" : "border-gray-800 hover:border-gray-700"}`}>
            <div className="flex items-center gap-2">
              <input type="radio" name="model" value={m.path} checked={selectedModel === m.path} onChange={() => setSelectedModel(m.path)} className="accent-blue-500" />
              <span className="text-sm text-gray-200">{m.filename}</span>
            </div>
            <span className="text-xs text-gray-600">{m.size_gb} GB</span>
          </label>
        ))}
      </Card>

      <Card>
        <p className="text-xs text-gray-500 mb-3">Contexte & GPU</p>
        <div className="space-y-4">
          <Slider label="n_ctx" value={nCtx} min={2048} max={32768} step={512} onChange={setNCtx} />
          <Slider label="n_gpu_layers  (-1 = tout)" value={nGpuLayers} min={-1} max={50} step={1} onChange={setNGpuLayers} />
        </div>
      </Card>

      <Card>
        <p className="text-xs text-gray-500 mb-3">Inférence</p>
        <div className="space-y-4">
          <Slider label="Temperature" value={temperature} min={0} max={1} step={0.05} onChange={setTemperature} />
          <Slider label="max_tokens" value={maxTokens} min={256} max={4096} step={128} onChange={setMaxTokens} />
          <Slider label="top_p" value={topP} min={0.5} max={1} step={0.05} onChange={setTopP} />
        </div>
      </Card>

      {status.status === "error" && status.error && (
        <p className="text-xs text-red-400 bg-red-950/20 border border-red-900 rounded p-3 mb-4">{status.error}</p>
      )}

      <button onClick={apply} disabled={saving || status.status === "loading"}
        className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-sm py-2 rounded-lg mb-6">
        {saving ? "Enregistrement…" : saved ? "Enregistré ✓" : "Appliquer"}
      </button>
    </>
  );
}

// ─── Corpus section ───────────────────────────────────────────────────────────

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
      {msg && <p className="text-xs text-green-400 mb-3">{msg}</p>}

      <Card>
        <div className="space-y-2">
          {list.map(c => (
            <div key={c.id}>
              <div className={`flex items-center justify-between rounded px-3 py-2 border ${c.id === active ? "border-blue-600/50 bg-blue-950/10" : "border-gray-800"}`}>
                <span className={`text-sm ${c.id === active ? "text-blue-300" : "text-gray-300"}`}>
                  {c.name}{c.id === active && <span className="ml-2 text-xs text-blue-500">actif</span>}
                </span>
                {c.id !== "default" && c.id !== active && (
                  <button onClick={() => { setDeleteTarget(c.id); setDeleteInput(""); }}
                    className="text-gray-700 hover:text-red-400 transition-colors p-1">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>

              {deleteTarget === c.id && (
                <div className="mt-1.5 rounded border border-red-900/50 bg-red-950/10 p-3">
                  <p className="text-xs text-red-400 mb-2">Tapez <span className="font-mono font-bold">"{c.name}"</span> pour confirmer la suppression :</p>
                  <div className="flex gap-2">
                    <input
                      value={deleteInput}
                      onChange={e => setDeleteInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Escape") { setDeleteTarget(null); setDeleteInput(""); } }}
                      className="flex-1 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-200 focus:outline-none focus:border-red-500"
                      autoFocus
                    />
                    <button onClick={() => del(c.id)} disabled={busy || deleteInput !== c.name}
                      className="text-xs bg-red-700 hover:bg-red-600 disabled:opacity-40 text-white px-3 py-1 rounded">
                      Supprimer
                    </button>
                    <button onClick={() => { setDeleteTarget(null); setDeleteInput(""); }}
                      className="text-xs text-gray-600 hover:text-gray-300 px-2">
                      ✕
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mt-3 pt-3 border-t border-gray-800">
          {creating ? (
            <div className="flex gap-2">
              <input ref={inputRef} value={nameInput} onChange={e => setNameInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(false); }}
                placeholder="Nom du corpus"
                className="flex-1 bg-gray-900 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500" />
              <button onClick={create} disabled={busy || !nameInput.trim()}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white px-3 py-1.5 rounded">
                {busy ? "…" : "Créer"}
              </button>
              <button onClick={() => { setCreating(false); setNameInput(""); }} className="text-xs text-gray-600 hover:text-gray-300 px-2">✕</button>
            </div>
          ) : (
            <button onClick={() => setCreating(true)}
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300">
              <Plus size={12} /> Nouveau corpus
            </button>
          )}
        </div>
      </Card>
    </>
  );
}

// ─── Santé section ────────────────────────────────────────────────────────────

function HealthSection() {
  const [stats, setStats] = useState<StatsData | null>(null);
  const [lint, setLint] = useState<LintResult | null>(null);
  const [linting, setLinting] = useState(false);

  useEffect(() => { fetch("/api/stats").then(r => r.json()).then(setStats); }, []);

  const runLint = async (autoFix = false) => {
    setLinting(true);
    setLint(await fetch(`/api/lint?auto_fix=${autoFix}`).then(r => r.json()));
    setLinting(false);
  };

  const totalIssues = lint ? lint.broken_links.length + lint.invalid_frontmatter.length + lint.orphans.length + lint.in_wiki_not_index.length + lint.in_index_not_wiki.length : 0;
  const pct = stats ? Math.round((stats.size.tokens / stats.budget) * 100) : 0;

  return (
    <>
      <SectionTitle>Santé du wiki</SectionTitle>

      {stats && (
        <Card>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[["Pages", stats.pages.total], ["Sources brutes", stats.raw_files], ["Entrées log", stats.log_entries]].map(([l, v]) => (
              <div key={l as string} className="text-center">
                <div className="text-lg font-bold text-gray-200">{v}</div>
                <div className="text-xs text-gray-600">{l}</div>
              </div>
            ))}
          </div>
          <div>
            <div className="flex justify-between text-xs mb-1">
              <span className="text-gray-600">Tokens wiki / budget</span>
              <span className={pct > 100 ? "text-yellow-400" : "text-gray-400"}>{pct}%</span>
            </div>
            <div className="w-full bg-gray-800 rounded-full h-1">
              <div className={`h-1 rounded-full ${pct > 100 ? "bg-yellow-500" : "bg-blue-500"}`} style={{ width: `${Math.min(pct, 100)}%` }} />
            </div>
          </div>
          {stats.last_log && <p className="text-xs text-gray-700 mt-2 truncate">{stats.last_log}</p>}
        </Card>
      )}

      <div className="flex gap-2 mb-3">
        <button onClick={() => runLint(false)} disabled={linting}
          className="flex-1 text-sm bg-gray-800 hover:bg-gray-700 disabled:opacity-40 text-gray-300 py-1.5 rounded-lg">
          {linting ? "Analyse…" : "Lancer lint"}
        </button>
        {lint && (
          <button onClick={() => runLint(true)} disabled={linting}
            className="text-sm bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white px-4 py-1.5 rounded-lg">
            Auto-fix
          </button>
        )}
      </div>

      {lint && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-gray-300">{lint.pages} pages · {lint.total_tokens.toLocaleString()} tokens</span>
            <span className={`text-sm font-bold ${totalIssues === 0 ? "text-green-400" : "text-red-400"}`}>
              {totalIssues === 0 ? "✓ OK" : `${totalIssues} problème${totalIssues > 1 ? "s" : ""}`}
            </span>
          </div>
          {[
            ["Liens cassés", lint.broken_links.map(b => `${b.page} → [[${b.target}]]`), "text-red-400"],
            ["Frontmatter invalide", lint.invalid_frontmatter.map(f => `${f.page}: ${f.reason}`), "text-yellow-400"],
            ["Pages orphelines", lint.orphans.map(s => `[[${s}]]`), "text-orange-400"],
            ["Absentes de l'index", lint.in_wiki_not_index.map(s => `[[${s}]]`), "text-blue-400"],
            ["Fantômes dans l'index", lint.in_index_not_wiki.map(s => `[[${s}]]`), "text-gray-500"],
          ].map(([title, items, color]) => (items as string[]).length > 0 && (
            <div key={title as string} className="mt-3">
              <p className={`text-xs font-semibold mb-1 ${color}`}>{title as string} ({(items as string[]).length})</p>
              <ul className="space-y-0.5">
                {(items as string[]).map((item, i) => (
                  <li key={i} className="text-xs text-gray-600 font-mono bg-gray-900 rounded px-2 py-1 truncate">{item}</li>
                ))}
              </ul>
            </div>
          ))}
        </Card>
      )}
    </>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function SettingsPanel({ onCorpusChange }: { onCorpusChange: () => void }) {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-xl mx-auto">
        <h2 className="text-base font-semibold text-gray-300 mb-5">Réglages</h2>
        <ModelSection />
        <hr className="border-gray-800 my-5" />
        <CorpusSection onCorpusChange={onCorpusChange} />
        <hr className="border-gray-800 my-5" />
        <HealthSection />
      </div>
    </div>
  );
}
