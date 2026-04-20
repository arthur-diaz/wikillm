import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import ChatPanel from "./components/ChatPanel";
import LibraryPanel from "./components/LibraryPanel";
import IngestPanel from "./components/IngestPanel";
import SettingsPanel from "./components/SettingsPanel";
import { CorpusContext, type Corpus } from "./CorpusContext";

export type Panel = "chat" | "library" | "ingest" | "settings";

export default function App() {
  const [active, setActive] = useState<Panel>("chat");
  const [corpusList, setCorpusList] = useState<Corpus[]>([]);
  const [activeCorpus, setActiveCorpus] = useState<string>("");
  const [corpusKey, setCorpusKey] = useState(0);

  const refreshCorpus = async () => {
    const data = await fetch("/api/corpus").then(r => r.json());
    setCorpusList(data.corpora);
    setActiveCorpus(data.active);
  };

  const switchCorpus = async (id: string) => {
    await fetch("/api/corpus/active", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: id }),
    });
    setActiveCorpus(id);
    setCorpusKey(k => k + 1);
  };

  useEffect(() => { refreshCorpus(); }, []);

  return (
    <CorpusContext.Provider value={{ active: activeCorpus, list: corpusList, refresh: refreshCorpus }}>
      <div className="flex h-screen overflow-hidden" style={{ background: "#0d1117" }}>
        <Sidebar active={active} onChange={setActive} activeCorpus={activeCorpus} corpusList={corpusList} onSwitchCorpus={switchCorpus} />
        <main className="flex-1 overflow-hidden">
          {active === "chat"     && <ChatPanel key={corpusKey} />}
          {active === "library"  && <LibraryPanel key={corpusKey} />}
          {active === "ingest"   && <IngestPanel key={corpusKey} />}
          {active === "settings" && <SettingsPanel key={corpusKey} onCorpusChange={() => { refreshCorpus(); setCorpusKey(k => k + 1); }} />}
        </main>
      </div>
    </CorpusContext.Provider>
  );
}
