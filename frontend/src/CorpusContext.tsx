import { createContext, useContext } from "react";

export type Corpus = { id: string; name: string };

export type CorpusCtx = {
  active: string;
  list: Corpus[];
  refresh: () => Promise<void>;
};

export const CorpusContext = createContext<CorpusCtx>({
  active: "",
  list: [],
  refresh: async () => {},
});

export const useCorpus = () => useContext(CorpusContext);
