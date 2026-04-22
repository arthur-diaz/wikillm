import { useState, useCallback } from "react";
import { Upload, CheckCircle, AlertCircle, Loader } from "lucide-react";
import { Card, SectionTitle } from "./ui/design";

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
    <div className="h-full overflow-y-auto p-6 ui-scroll" style={{ background: "#0a0a0b" }}>
      <div className="max-w-xl mx-auto">
        <SectionTitle>Ingest</SectionTitle>

        <label
          className={`flex flex-col items-center justify-center border border-dashed rounded-xl p-10 mb-5 cursor-pointer transition-colors ${
            dragging
              ? "border-[#8b5cf6] bg-[#8b5cf6]/5"
              : "border-[#212226] hover:border-[#2a2b31] bg-[#111113]"
          }`}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <div className="w-10 h-10 rounded-lg border border-[#212226] bg-[#17181b] grid place-items-center text-[#6b6c72] mb-3">
            <Upload size={18} />
          </div>
          <p className="text-[13px] text-[#ececed]">Dépose un fichier .md ici</p>
          <p className="text-[11px] text-[#6b6c72] mt-1">ou clique pour sélectionner</p>
          <input
            type="file"
            className="hidden"
            accept=".md,.txt,.html"
            onChange={e => { const f = e.target.files?.[0]; if (f) processFile(f); }}
          />
        </label>

        {steps.length > 0 && (
          <div className="space-y-1.5 mb-4">
            {steps.map((s, i) => (
              <div key={i} className="flex items-start gap-2 text-[13px]">
                {s.step === "done"   ? <CheckCircle size={13} className="text-[#34d399] mt-0.5 flex-shrink-0" />
                : s.step === "error" ? <AlertCircle size={13} className="text-red-400 mt-0.5 flex-shrink-0" />
                : loading && i === steps.length - 1
                                     ? <Loader size={13} className="text-[#a78bfa] mt-0.5 flex-shrink-0 animate-spin" />
                                     : <CheckCircle size={13} className="text-[#3a3b42] mt-0.5 flex-shrink-0" />}
                <span className={s.step === "error" ? "text-red-400" : "text-[#a1a1a6]"}>{s.msg}</span>
              </div>
            ))}
          </div>
        )}

        {done?.data && (
          <Card>
            <p className="font-semibold text-[#ececed] text-[13px] mb-1">{done.data.title as string}</p>
            <p className="text-[#a1a1a6] mb-3 text-[12px] leading-relaxed">{done.data.summary as string}</p>
            <div className="flex gap-4 text-[11px] mb-3">
              <span className="text-[#a78bfa]">{done.data.created as number} créées</span>
              <span className="text-[#6b6c72]">{done.data.enriched as number} enrichies</span>
              {(done.data.contradictions as string[]).length > 0 && (
                <span className="text-yellow-400">{(done.data.contradictions as string[]).length} contradictions</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {(done.data.entities as string[]).map(e => (
                <span key={e} className="text-[11px] bg-[#a78bfa]/12 text-[#c4b5fd] border border-[#a78bfa]/30 px-2 py-0.5 rounded">{e}</span>
              ))}
              {(done.data.concepts as string[]).map(c => (
                <span key={c} className="text-[11px] bg-[#34d399]/12 text-[#6ee7b7] border border-[#34d399]/30 px-2 py-0.5 rounded">{c}</span>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
