import { useState, useCallback } from "react";
import { Upload, CheckCircle, AlertCircle, Loader } from "lucide-react";

type Step = { step: string; msg: string; data?: Record<string, unknown> };

export default function IngestPanel() {
  const [dragging, setDragging] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [loading, setLoading] = useState(false);

  const processFile = async (file: File) => {
    setSteps([]); setLoading(true);
    const body = new FormData();
    body.append("file", file);
    const response = await fetch("/api/ingest", { method: "POST", body });
    if (!response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        if (line.startsWith("data: ") && line.length > 6) {
          try { setSteps(p => [...p, JSON.parse(line.slice(6))]); } catch {}
        }
      }
    }
    setLoading(false);
  };

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) processFile(f);
  }, []);

  const done = steps.find(s => s.step === "done");

  return (
    <div className="p-6 max-w-xl mx-auto">
      <h2 className="text-base font-semibold text-gray-300 mb-4">Ingest</h2>

      <label
        className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-10 mb-5 cursor-pointer transition-colors ${
          dragging ? "border-blue-400 bg-blue-900/10" : "border-gray-700 hover:border-gray-600"
        }`}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <Upload size={28} className="text-gray-600 mb-2" />
        <p className="text-sm text-gray-400">Dépose un fichier .md ici</p>
        <p className="text-xs text-gray-600 mt-1">ou clique pour sélectionner</p>
        <input type="file" className="hidden" accept=".md,.txt,.html" onChange={e => { const f = e.target.files?.[0]; if (f) processFile(f); }} />
      </label>

      {steps.length > 0 && (
        <div className="space-y-1.5 mb-4">
          {steps.map((s, i) => (
            <div key={i} className="flex items-start gap-2 text-sm">
              {s.step === "done"  ? <CheckCircle size={14} className="text-green-400 mt-0.5 flex-shrink-0" />
              : s.step === "error" ? <AlertCircle size={14} className="text-red-400 mt-0.5 flex-shrink-0" />
              : loading && i === steps.length - 1 ? <Loader size={14} className="text-blue-400 mt-0.5 flex-shrink-0 animate-spin" />
              : <CheckCircle size={14} className="text-gray-700 mt-0.5 flex-shrink-0" />}
              <span className={s.step === "error" ? "text-red-400" : "text-gray-300"}>{s.msg}</span>
            </div>
          ))}
        </div>
      )}

      {done?.data && (
        <div className="rounded-lg border border-gray-800 p-4 text-sm" style={{ background: "#161b22" }}>
          <p className="font-medium text-gray-200 mb-1">{done.data.title as string}</p>
          <p className="text-gray-500 mb-3 text-xs">{done.data.summary as string}</p>
          <div className="flex gap-4 text-xs mb-2">
            <span className="text-blue-400">{done.data.created as number} créées</span>
            <span className="text-gray-500">{done.data.enriched as number} enrichies</span>
            {(done.data.contradictions as string[]).length > 0 && (
              <span className="text-yellow-400">{(done.data.contradictions as string[]).length} contradictions</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {(done.data.entities as string[]).map(e => <span key={e} className="text-xs bg-purple-900/30 text-purple-300 px-2 py-0.5 rounded">{e}</span>)}
            {(done.data.concepts as string[]).map(c => <span key={c} className="text-xs bg-green-900/30 text-green-300 px-2 py-0.5 rounded">{c}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}
