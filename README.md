# LLM Wiki

Base de connaissances personnelle maintenue par un LLM local (Gemma 3n E4B IT via llama.cpp).

## Installation

### 1. Dépendances normales

```bash
uv sync
```

### 2. llama-cpp-python avec CUDA (RTX 3070 Ti)

**Ne pas utiliser `uv add`** — cela installe la version CPU. Utiliser la wheel pré-compilée :

```bash
uv pip install llama-cpp-python==0.3.4 --force-reinstall --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
```

> Vérifie ta version CUDA avec `nvidia-smi`. Adapte `cu121` si besoin (`cu122`, `cu123`, `cu124`).

> **Important** : après chaque `uv add <paquet>`, relancer cette commande — `uv sync` réinstalle la version CPU.

### 3. Télécharger le modèle

```bash
uv run huggingface-cli download unsloth/gemma-3n-E4B-it-GGUF \
  gemma-3n-E4B-it-Q4_K_M.gguf \
  --local-dir models/
```

Puis mettre à jour `config.yaml` :

```yaml
model:
  path: "models/gemma-3n-E4B-it-Q4_K_M.gguf"
```

### 4. Vérifier que le GPU est utilisé

Activer `verbose: true` dans `config.yaml` pour le premier lancement. Chercher :

```
llm_load_tensors: offloaded 33/33 layers to GPU
```

Si `0/33` → la wheel CUDA n'est pas installée, reprendre l'étape 2.

## Utilisation

```bash
# Mode interactif (charge le modèle une seule fois — recommandé)
uv run wiki shell

# Ou commandes directes
uv run wiki ingest "data/RAG/Clippings/mon-article.md"
uv run wiki query "ma question"
uv run wiki lint
uv run wiki stats
```

Voir `SCHEMA.md` pour les conventions du wiki.
