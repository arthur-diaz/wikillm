import { useEffect, useRef, useState } from "react";
import { CheckCircle, AlertCircle, Loader, Plus, Trash2 } from "lucide-react";
import { useCorpus } from "../CorpusContext";
import { Card, SectionTitle, Slider, BtnPrimary, BtnSecondary, TextInput, ACCENT } from "./ui/design";

type ModelFile = { filename: string; path: string; size_gb: number; active: boolean };
type Settings = { model: { path: string; n_ctx: number; n_gpu_layers: number }; inference: { temperature: number; max_tokens: number; top_p: number } };
type ModelStatus = { status: "loaded" | "loading" | "error"; error: string | null };
type LintResult = { pages: number; total_tokens: number; broken_links: { page: string; target: string }[]; invalid_frontmatter: { page: string; reason: string }[]; orphans: string[]; in_wiki_not_index: string[]; in_index_not_wiki: string[] };
type StatsData = { raw_files: number; pages: { total: number; sources: number; entities: number; concepts: number }; size: { tokens: number }; budget: number; log_entries: number; last_log: string | null };

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
      method: "PUT", headers: { "Content-Type": "application/json" },
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
          <span className="text-[11px] text-[#6b6c72] uppercase tracking-wide">Statut</span>
          {status.status === "loading" && <span className="flex items-center gap-1.5 text-[11px] text-yellow-400"><Loader size={11} className="animate-spin" /> Chargement…</span>}
          {status.status === "error"   && <span className="flex items-center gap-1.5 text-[11px] text-red-400"><AlertCircle size={11} /> Erreur</span>}
          {status.status === "loaded"  && <span className="flex items-center gap-1.5 text-[11px] text-[#34d399]"><CheckCircle size={11} /> Prêt</span>}
        </div>
        {models.length === 0 ? (
          <p className="text-[11px] text-[#6b6c72]">Aucun fichier .gguf dans models/</p>
        ) : models.map(m => (
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

      <BtnPrimary onClick={apply} disabled={saving || status.status === "loading"} className="w-full mb-6">
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

      <div className="flex gap-2 mb-3">
        <BtnSecondary onClick={() => runLint(false)} disabled={linting} className="flex-1">
          {linting ? "Analyse…" : "Lancer lint"}
        </BtnSecondary>
        {lint && <BtnPrimary onClick={() => runLint(true)} disabled={linting}>Auto-fix</BtnPrimary>}
      </div>

      {lint && (
        <Card>
          <div className="flex items-center justify-between mb-3">
            <span className="text-[13px] text-[#ececed]">{lint.pages} pages · {lint.total_tokens.toLocaleString()} tokens</span>
            <span className={`text-[13px] font-semibold ${totalIssues === 0 ? "text-[#34d399]" : "text-red-400"}`}>
              {totalIssues === 0 ? "✓ OK" : `${totalIssues} problème${totalIssues > 1 ? "s" : ""}`}
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
        </Card>
      )}
    </>
  );
}

export default function SettingsPanel({ onCorpusChange }: { onCorpusChange: () => void }) {
  return (
    <div className="h-full overflow-y-auto p-6 ui-scroll" style={{ background: "#0a0a0b" }}>
      <div className="max-w-xl mx-auto">
        <h2 className="text-[15px] font-semibold text-[#ececed] mb-5">Réglages</h2>
        <ModelSection />
        <hr className="border-[#212226] my-5" />
        <CorpusSection onCorpusChange={onCorpusChange} />
        <hr className="border-[#212226] my-5" />
        <HealthSection />
      </div>
    </div>
  );
}
