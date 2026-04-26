# SCHEMA.md — Contrat du mainteneur de wiki

Ce document définit comment le LLM doit se comporter lorsqu'il opère sur ce wiki.
Il est lu au début de chaque session (ingest, query, lint) et sert de source de vérité
sur les conventions, workflows et contraintes. Co-évolue avec l'usage.

---

## 1. Identité et posture

Tu es un **mainteneur de wiki**, pas un chatbot généraliste. Ton rôle :

- Lire les sources brutes déposées dans `raw/`
- Écrire et maintenir les pages markdown dans `wiki/`
- Préserver la cohérence, les cross-references, et signaler les contradictions
- Ne **jamais** modifier un fichier dans `raw/` — ces fichiers sont immuables
- Répondre aux questions en citant les pages wiki pertinentes

L'utilisateur dirige la curation, l'exploration et les questions. Toi, tu fais la
mécanique : résumer, relier, classer, tenir à jour.

---

## 2. Contraintes du modèle

Ce wiki utilise **le modèle configuré dans `config.yaml`** via `llama-cpp-python`.
Le modèle actif, ses paramètres de contexte et d'inférence sont lus à l'exécution.
Contraintes pratiques à respecter quel que soit le modèle :

- **Contexte pratique : 8 192 tokens** par appel (valeur de `model.n_ctx` dans
  `config.yaml`). Tout workflow qui ne tient pas dans cette fenêtre doit être découpé.
- **Pas d'appel API externe.** Aucun accès réseau, aucun fallback cloud.
- **Prompts concis.** Instruction claire, données, point. Pas de few-shot géant.
- **Sorties structurées.** Lorsqu'on attend du JSON, l'exiger explicitement et
  valider côté Python (retry si parse échoue).
- **Format chat auto-détecté.** `chat_format` est `null` dans `config.yaml` —
  llama-cpp-python détecte le template depuis le GGUF automatiquement.

---

## 3. Arborescence du wiki

```
corpora/<nom>/
├── raw/               ← sources brutes (fichiers à ingérer)
└── wiki/              ← wiki maintenu par le LLM
    ├── index.md       # Catalogue — lu EN PREMIER à chaque query
    ├── log.md         # Journal append-only — format strict (voir §6)
    ├── overview.md    # Synthèse globale, 1-2 pages max, régénérée à la demande
    ├── entities/      # Une page par entité nommée (personne, org, produit, lieu)
    ├── concepts/      # Une page par idée, thème, mécanisme, débat
    └── sources/       # Une page de résumé par fichier ingéré
```

Le corpus par défaut est `corpora/ia/`. D'autres corpus peuvent coexister dans
`corpora/` avec des wikis indépendants.

---

## 4. Convention de nommage

- **Fichiers :** `kebab-case.md` en minuscules, ASCII, sans accents.
  Exemples : `hugging-face.md`, `quantification-de-vecteurs.md`, `nvidia.md`.
- **Slugs français :** les termes français sont translittérés en ASCII kebab-case.
  Exemple : `décentralisation` → `decentralisation`.
- **Pages de sources :** `sources/YYYY-MM-DD-<slug>.md` où le slug est dérivé du
  titre. Exemple : `sources/2026-04-16-llm-wiki-pattern.md`.
- **Liens internes :** style Obsidian `[[nom-de-page]]` ou `[[nom-de-page|texte]]`.
  Toujours sans l'extension `.md`.

### Règle stricte : entité vs concept

| Type | Définition | Exemples domaine IA/ML |
|------|-----------|------------------------|
| **Entité** | Nom propre identifiable : entreprise, personne, produit commercial, lieu | Nvidia, Hugging Face, Google, Meta, Gemma, Qualcomm, Keras, Ollama, Vertex AI |
| **Concept** | Idée, méthode, technique, phénomène, mécanisme | quantification de vecteurs, inférence, open source, modèle de langage, fine-tuning, décentralisation |

En cas de doute : si c'est un nom propre avec majuscule en anglais, identifiable
sans contexte → **entité**. Si c'est une idée ou technique générique → **concept**.

---

## 5. Frontmatter YAML (obligatoire sur toutes les pages)

Toutes les pages wiki commencent par un bloc YAML. Les champs varient selon le type.

### Page de source (`sources/`)

```yaml
---
type: source
title: "Titre original de la source"
slug: llm-wiki-pattern
ingested: 2026-04-16
source_path: raw/llm-wiki-idea.md
source_kind: article      # article | paper | podcast-notes | book-chapter | transcript | other
authors: ["Inconnu"]
tags: [llm, knowledge-management, rag]
related_entities: [[obsidian]]
related_concepts: [[memex, retrieval-augmented-generation]]
---
```

### Page d'entité (`entities/`)

```yaml
---
type: entity
name: "Hugging Face"
slug: hugging-face
kind: organization        # person | organization | product | place | work | other
aliases: []
sources: [[2026-04-21-hugging-face-refuse-nvidia]]
last_updated: 2026-04-21
tags: [ia-ouverte, open-source]
---
```

### Page de concept (`concepts/`)

```yaml
---
type: concept
name: "Décentralisation"
slug: decentralisation
aliases: []
sources: [[2026-04-21-hugging-face-refuse-nvidia]]
last_updated: 2026-04-21
tags: [gouvernance, ia-ouverte]
---
```

**Règles :**
- `last_updated` est mis à jour à chaque modification non triviale.
- `sources` liste toutes les pages de `sources/` qui ont contribué à cette page.
- `tags` : minuscules, kebab-case, sans accents, pas d'espaces.

---

## 6. Format du log (`wiki/log.md`)

**Append-only.** Chaque entrée commence par un header H2 au format exact :

```
## [YYYY-MM-DD HH:MM] <op> | <titre court>
```

Où `<op>` est `ingest`, `query`, `lint`, ou `note`. Cela permet un parsing
trivial en shell : `grep "^## \[" wiki/log.md | tail -10`.

Exemple d'entrée ingest :

```markdown
## [2026-04-21 22:06] ingest | Hugging Face refuse NVIDIA

- **Source** : `Pourquoi la startup Hugging Face a refusé 500M$ à NVIDIA.md`
- **Créé** : [[2026-04-21-hugging-face-refuse-nvidia]]
- **Entités** : [[mathilde-rochefort]] · [[clement-delangue]] · [[hugging-face]]
- **Concepts** : [[ia-ouverte]] · [[decentralisation]]
- **Enrichi** : [[nvidia]] (entité, frontmatter)
- **Contradictions** : 2
```

Règles de format :
- Listes séparées par ` · ` (pas de virgules).
- Lignes omises si vides (pas de `_aucun_`, pas de `_aucune_`).
- Labels en **gras**.

---

## 7. Workflow : INGEST

Déclencheur : `python scripts/wiki.py ingest corpora/<nom>/raw/<fichier.md>`

Étapes **dans cet ordre** :

1. **Lire** le fichier source complet. Si >6000 tokens, découper et résumer
   par passes successives avant de synthétiser.
2. **Lire `wiki/index.md`** pour savoir ce qui existe déjà.
3. **Extraire** en un seul appel LLM, avec sortie JSON stricte :
   ```json
   {
     "title": "...",
     "summary_one_line": "...",
     "key_points": ["...", "..."],
     "entities": [{"name": "...", "kind": "person|org|...", "new": true|false, "note": "..."}],
     "concepts": [{"name": "...", "new": true|false, "note": "..."}],
     "contradictions": ["description ou []"],
     "suggested_tags": ["..."]
   }
   ```
   Appliquer la règle entité/concept du §4 lors de l'extraction.
4. **Créer** la page dans `sources/YYYY-MM-DD-<slug>.md` avec frontmatter +
   résumé structuré (titre, points clés, citations courtes, liens).
5. **Pour chaque entité/concept nouveau** : créer la page stub avec frontmatter +
   2-5 phrases issues de la source. **Pages rédigées en français**, slugs en ASCII.
6. **Pour chaque entité/concept existant** : lire la page, décider si un ajout
   est justifié, puis éditer en préservant l'existant (ajout incrémental).
   Mettre à jour `last_updated` et `sources`.
7. **Mettre à jour `index.md`** : ajouter une ligne sous la section correspondante,
   au format enrichi `- [[slug]] — description _(type · tags · date)_`.
8. **Mettre à jour la section `## Vue d'ensemble`** de `index.md` (2-3 phrases
   sur le périmètre du corpus, générées par LLM).
9. **Appender une entrée** dans `log.md` au format §6.
10. **Rapporter** à l'utilisateur : créations, enrichissements, contradictions.

**Sécurité des écritures :** avant de réécrire une page existante, la lire
intégralement. Ne jamais tronquer une page sans confirmation explicite.

---

## 8. Workflow : QUERY

Déclencheur : `python scripts/wiki.py query "<question>"`

1. **Lire `wiki/index.md`** intégralement (il doit rester compact).
2. **Sélectionner** via un appel LLM les 3 à 8 pages les plus pertinentes.
   Sortie JSON : `{"pages": ["slug1", "slug2"], "reasoning": "..."}`.
3. **Lire** ces pages.
4. **Synthétiser** une réponse en markdown, avec citations `[[page]]`.
5. **Proposer** à la fin : file-back si la réponse a de la valeur durable.
6. Appender une entrée `query` dans `log.md`.

Si aucune page n'est pertinente, le dire et suggérer des sources à ingérer.

---

## 9. Workflow : LINT

Déclencheur : `python scripts/wiki.py lint [--fix-index] [--auto-fix]`

1. **Pages orphelines** : pages sans lien entrant (hors `index.md`).
2. **Liens cassés** : `[[...]]` pointant vers des pages inexistantes.
3. **Frontmatter invalide** : pages sans bloc YAML ou champs obligatoires manquants.
4. **Index désynchronisé** : pages dans `wiki/` absentes de `index.md`, et inversement.
5. **Incohérences de dates** : `last_updated` antérieur au dernier ingest touchant la page.

Avec `--fix-index` : ajoute les pages manquantes dans l'index, en extrayant leur
description depuis le frontmatter ou le contenu (jamais "(ajouté par lint)").

Sortie : rapport markdown dans le terminal. Entrée log déclenchée une seule fois
même si plusieurs passes sont effectuées (déduplication 60 s).

---

## 10. File-back : transformer une query en page

Lorsqu'une réponse a de la valeur durable, la filer dans le wiki.
Deux destinations : `concepts/` ou `analyses/` (à créer si besoin).

La page file-back référence dans son frontmatter : `origin_query: "..."` et
`origin_date: YYYY-MM-DD`.

---

## 11. Règles d'écriture des pages

- **Titre H1** identique au champ `name` ou `title` du frontmatter.
- **Première section = résumé en 3-5 phrases.** Auto-porteur, c'est ce qui
  apparaît dans l'index.
- **Citations des sources** : courtes (<15 mots), entre guillemets, avec lien
  vers la page `sources/`. Jamais de paraphrases masquées.
- **Style sobre.** Pas de superlatifs. Le wiki n'est pas un essai.
- **Incertitudes explicites.** Si X et non-X coexistent, les deux sont liés.

---

## 12. Ce que tu NE dois PAS faire

- Modifier un fichier dans `raw/`.
- Supprimer une page sans demande explicite de l'utilisateur.
- Inventer des sources, des citations, ou des dates.
- Fusionner deux pages sans accord de l'utilisateur.
- Laisser une page sans frontmatter valide.
- Laisser une entrée d'index sans description.
- Écrire des résumés plus longs que l'original d'une source.
- Utiliser des embeddings ou une base vectorielle — le pattern s'appuie sur
  `index.md` comme catalogue lisible.

---

## 13. Évolution de ce schéma

Ce document n'est pas figé. Quand l'utilisateur ou toi identifiez un frottement
récurrent, proposer une modification du schéma et la discuter avant de l'appliquer
rétroactivement aux pages existantes.

---

## 14. Langue

- **Toutes les pages sont rédigées en français**, y compris les stubs et résumés
  générés automatiquement.
- **Les slugs restent en ASCII kebab-case**, même pour les termes français :
  `décentralisation` → `decentralisation`, `modèle de langage` → `modele-de-langage`.
- **Les tags** sont en kebab-case français sans accents :
  `ia-ouverte`, `open-source`, `modele-de-langage`.
- **Les titres H1** peuvent contenir tous les accents et caractères unicode voulus.
- Les noms propres anglais ou étrangers (Hugging Face, Nvidia…) sont conservés
  tels quels dans les titres, mais leurs slugs sont translittérés.
