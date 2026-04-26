# WikiLLM

WikiLLM est une base de connaissances personnelle maintenue par un LLM. Il ingère des sources brutes (articles, notes, transcripts), en extrait entités et concepts, et maintient des pages Markdown interconnectées dans un vault Obsidian. Une interface web React pilote l'ensemble sans CLI.

> **Backends supportés** : modèle GGUF local (llama.cpp), OpenAI, Anthropic, Mistral.

---

## Aperçu

![Interface WikiLLM](docs/screenshot.png)

*(screenshot à venir)*

---

## Installation

### Prérequis

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (`pip install uv`)
- Node.js 18+ (pour le frontend)
- GPU NVIDIA recommandé pour les modèles locaux

### 1. Cloner et installer les dépendances Python

```bash
git clone https://github.com/<ton-user>/wikillm.git
cd wikillm
uv sync
```

### 2. Configurer

```bash
cp config.yaml.example config.yaml
cp workspaces.json.example workspaces.json
```

Édite `config.yaml` : choisis le mode (`local` ou `api`), renseigne le chemin GGUF ou les clés API.

### 3. Modèle local (optionnel, si `mode: local`)

Télécharger un modèle GGUF dans `models/` :

```bash
uv run python download_model.py
```

Ou manuellement depuis [HuggingFace](https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF) et placer le fichier `.gguf` dans `models/`.

#### llama-cpp-python avec CUDA

```bash
uv pip install llama-cpp-python==0.3.4 --force-reinstall --no-cache-dir \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

> Adapte `cu121` à ta version CUDA (`nvidia-smi` pour vérifier). Relancer cette commande après chaque `uv add`.

### 4. Frontend

```bash
cd frontend
npm install
npm run build
```

Le build est copié dans `app/static/` et servi automatiquement par FastAPI.

---

## Lancement

```bash
# Backend FastAPI (port 8000)
uvicorn app.main:app --reload --port 8000

# Frontend dev avec hot-reload (port 5173) — facultatif si le build existe
cd frontend && npm run dev
```

Ouvrir [http://localhost:5173](http://localhost:5173) (dev) ou [http://localhost:8000](http://localhost:8000) (prod).

### CLI

```bash
uv run wiki shell          # mode interactif (charge le modèle une seule fois)
uv run wiki ingest raw/mon-article.md
uv run wiki query "ma question"
uv run wiki search "termes"
uv run wiki lint [--auto-fix]
uv run wiki stats
uv run wiki suggest
```

---

## Interface web

| Panneau | Description |
|---|---|
| **Query** | Question → réponse LLM ancrée dans le wiki |
| **Ingest** | Upload fichier → extraction LLM → pages wiki |
| **Search** | Recherche full-text BM25 |
| **Wiki** | Navigateur de pages Markdown |
| **Graph** | Graphe de liens force-directed |
| **Lint** | Liens cassés, frontmatter invalide, pages orphelines |
| **Stats** | Compteurs, budget tokens, tags fréquents |
| **Suggest** | Le LLM propose des pistes d'exploration |
| **Corpus** | Plusieurs bases de connaissances indépendantes |
| **Paramètres** | Modèle GGUF, sliders d'inférence, clés API |

---

## Architecture

WikiLLM organise les données en trois couches :

```
raw/              ← sources brutes (articles, notes, PDF texte…)
wiki/
  sources/        ← page de synthèse par source ingérée
  entities/       ← personnes, organisations, produits…
  concepts/       ← idées, théories, techniques…
  index.md        ← sommaire navigable maintenu par le LLM
  log.md          ← journal d'opérations
SCHEMA.md         ← conventions lues par le LLM à chaque appel
```

Chaque page Markdown contient un frontmatter YAML (type, slug, tags, liens) et un corps en prose. Le LLM enrichit les pages existantes plutôt que d'en créer des doublons.

---

## Backends LLM supportés

| Backend | Config `mode` | Prérequis |
|---|---|---|
| GGUF local (llama.cpp) | `local` | fichier `.gguf` dans `models/` |
| OpenAI | `api` + `api_provider: openai` | clé `OPENAI_API_KEY` dans `config.yaml` |
| Anthropic | `api` + `api_provider: anthropic` | clé `ANTHROPIC_API_KEY` |
| Mistral | `api` + `api_provider: mistral` | clé `MISTRAL_API_KEY` |

---

## Multi-corpus

Chaque corpus est un wiki indépendant avec ses propres dossiers `raw/` et `wiki/`. Le panneau **Corpus** permet de créer et switcher entre eux. Les corpus sont enregistrés dans `workspaces.json` (non versionné).

Pour ouvrir un corpus dans Obsidian : **"Ouvrir un autre coffre"** → `corpora/<slug>/wiki/`.

---

## Structure du projet

```
wikillm/
├── app/                    # Backend FastAPI
│   ├── main.py
│   ├── state.py
│   ├── wiki_bridge.py
│   └── routers/
├── frontend/               # Interface React/Vite/Tailwind
├── scripts/
│   └── wiki.py             # CLI + logique métier
├── SCHEMA.md               # Conventions du wiki (lues par le LLM)
├── config.yaml.example     # Template de configuration → copier en config.yaml
├── workspaces.json.example # Template corpus → copier en workspaces.json
├── download_model.py       # Helper pour télécharger un GGUF depuis HuggingFace
├── requirements.txt
└── pyproject.toml
```

---

## Licence

MIT
