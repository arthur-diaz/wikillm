# LLM Wiki

Base de connaissances personnelle maintenue par un LLM local (Meta-Llama-3.1-8B-Instruct Q4_K_M via llama.cpp).

Le wiki est un vault Obsidian versionné : le LLM ingère des sources brutes, extrait entités et concepts, et maintient des pages markdown interconnectées. Une interface web permet de tout piloter sans CLI.

## Installation

### 1. Dépendances Python

```bash
uv sync
```

### 2. llama-cpp-python avec CUDA

**Ne pas utiliser `uv add`** — cela installe la version CPU. Utiliser la wheel pré-compilée :

```bash
uv pip install llama-cpp-python==0.3.4 --force-reinstall --no-cache-dir \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

> Vérifie ta version CUDA avec `nvidia-smi`. Adapte `cu121` si besoin (`cu122`, `cu123`, `cu124`).

> **Important** : après chaque `uv add <paquet>`, relancer cette commande — `uv sync` réinstalle la version CPU.

### 3. Modèle GGUF

Télécharger et placer dans `models/` :

```bash
uv run huggingface-cli download bartowski/Meta-Llama-3.1-8B-Instruct-GGUF \
  Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
  --local-dir models/
```

Le chemin dans `config.yaml` est déjà configuré pour ce fichier.

### 4. Frontend (optionnel, pour l'interface web)

```bash
cd frontend
npm install
npm run build
```

Le build est copié dans `app/static/` et servi automatiquement par FastAPI.

En développement, lancer le serveur Vite séparément (voir ci-dessous).

### 5. Vérifier l'utilisation GPU

Activer `verbose: true` dans `config.yaml` pour le premier lancement. Chercher :

```
llm_load_tensors: offloaded 33/33 layers to GPU
```

Si `0/33` → la wheel CUDA n'est pas installée, reprendre l'étape 2.

## Lancer l'application

### Interface web (recommandé)

```bash
# Backend FastAPI (port 8000)
uvicorn app.main:app --reload --port 8000

# Frontend dev avec hot-reload (port 5173) — optionnel si le build existe
cd frontend && npm run dev
```

Ouvrir [http://localhost:5173](http://localhost:5173) (dev) ou [http://localhost:8000](http://localhost:8000) (prod).

### CLI uniquement

```bash
# Mode interactif (charge le modèle une seule fois — recommandé)
uv run wiki shell

# Commandes directes
uv run wiki ingest raw/mon-article.md
uv run wiki query "ma question"
uv run wiki search "termes"
uv run wiki lint [--auto-fix]
uv run wiki stats
uv run wiki suggest
```

## Interface web — fonctionnalités

| Panneau | Description |
|---|---|
| **Query** | Pose une question, le LLM répond en s'appuyant sur les pages du wiki |
| **Ingest** | Upload d'un fichier → extraction LLM → création des pages wiki |
| **Search** | Recherche full-text BM25 dans toutes les pages |
| **Wiki** | Navigateur de pages avec lecture markdown |
| **Graph** | Graphe de liens entre pages (force-directed SVG) |
| **Lint** | Détecte liens cassés, frontmatter invalide, pages orphelines |
| **Stats** | Compteurs, budget tokens, tags fréquents |
| **Suggest** | Le LLM analyse le wiki et propose des pistes d'exploration |
| **Corpus** | Gère plusieurs bases de connaissances indépendantes |
| **Paramètres** | Sélection du modèle GGUF, sliders d'inférence (n_ctx, temperature…) |

## Multi-corpus

Chaque corpus est un wiki indépendant avec ses propres dossiers `raw/` et `wiki/`. Le panneau **Corpus** permet de créer et switcher entre eux.

À la création, le système génère automatiquement :

```
corpora/<slug>/
  raw/                  ← sources brutes à ingérer
  wiki/
    .obsidian/          ← vault Obsidian prêt à l'emploi
    sources/
    entities/
    concepts/
    index.md
    log.md
```

Pour ouvrir un corpus dans Obsidian : **"Ouvrir un autre coffre"** → `corpora/<slug>/wiki/`.

Les corpus sont enregistrés dans `workspaces.json`.

## Structure du projet

```
.
├── scripts/wiki.py       # CLI + logique métier (ingest, query, lint…)
├── app/
│   ├── main.py           # Serveur FastAPI
│   ├── state.py          # Corpus actif, statut modèle
│   ├── wiki_bridge.py    # Bridge async ↔ sync pour le LLM
│   └── routers/          # Endpoints API
├── frontend/             # Interface React/Vite/Tailwind
├── config.yaml           # Modèle et paramètres d'inférence
├── workspaces.json       # Registre des corpus
├── SCHEMA.md             # Conventions du wiki (lu par le LLM)
├── models/               # Fichiers GGUF (non versionnés)
├── raw/                  # Sources brutes corpus par défaut (non versionné)
└── data/RAG/             # Vault Obsidian corpus par défaut
    └── wiki/             # Pages maintenues par le LLM
```

## Configuration (`config.yaml`)

```yaml
model:
  path: "models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
  n_gpu_layers: -1    # -1 = toutes les couches sur GPU
  n_ctx: 8192         # fenêtre de contexte
  n_batch: 512
  chat_format: null   # template embarqué dans le GGUF
  verbose: false

inference:
  temperature: 0.3
  top_p: 0.9
  max_tokens: 2048
```

Voir `SCHEMA.md` pour les conventions de nommage et de structure des pages wiki.
