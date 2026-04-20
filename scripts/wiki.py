"""
wiki.py — CLI pour le LLM Wiki local (v3).

Usage (depuis la racine du projet) :
    python scripts/wiki.py ingest raw/mon-article.md [--batch]
    python scripts/wiki.py batch-ingest [raw/sous-dossier] [--dry-run]
    python scripts/wiki.py query "question" [--file-back]
    python scripts/wiki.py search <termes> [-n 10]
    python scripts/wiki.py lint [--fix-index] [--auto-fix]
    python scripts/wiki.py overview
    python scripts/wiki.py stats
    python scripts/wiki.py export <slug> [--format html|marp]
    python scripts/wiki.py shell

Le script cherche config.yaml et SCHEMA.md à la racine du projet (un niveau
au-dessus de scripts/).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import click
import frontmatter
import yaml

# llama_cpp est importé paresseusement dans _get_llm() pour que les commandes
# qui n'appellent pas le LLM (lint) démarrent sans charger CUDA.

# ---------------------------------------------------------------------------
# Chemins et configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "SCHEMA.md"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        click.echo(f"[erreur] config.yaml introuvable à {CONFIG_PATH}")
        sys.exit(1)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG = load_config()
WIKI_DIR = PROJECT_ROOT / CFG["paths"]["wiki_dir"]
RAW_DIR = PROJECT_ROOT / CFG["paths"]["raw_dir"]
INDEX_FILE = PROJECT_ROOT / CFG["paths"]["index_file"]
LOG_FILE = PROJECT_ROOT / CFG["paths"]["log_file"]
OVERVIEW_FILE = WIKI_DIR / "overview.md"


# ---------------------------------------------------------------------------
# WikiContext — chemins dynamiques pour le multi-corpus
# ---------------------------------------------------------------------------

@dataclass
class WikiContext:
    wiki_dir: Path
    raw_dir: Path
    index_file: Path
    log_file: Path
    overview_file: Path

    @classmethod
    def from_paths(cls, wiki_dir: Path, raw_dir: Path) -> "WikiContext":
        return cls(
            wiki_dir=wiki_dir,
            raw_dir=raw_dir,
            index_file=wiki_dir / "index.md",
            log_file=wiki_dir / "log.md",
            overview_file=wiki_dir / "overview.md",
        )


DEFAULT_CTX: WikiContext = WikiContext.from_paths(WIKI_DIR, RAW_DIR)


# ---------------------------------------------------------------------------
# Budget tokens — estimation légère sans tokenizer externe
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimation grossière : ~4 caractères = 1 token en anglais/français.
    Suffisant pour le budget management, pas pour la facturation."""
    return len(text) // 4


def token_budget() -> int:
    """Tokens disponibles pour le contenu (prompt + réponse), en laissant une
    marge de sécurité de 20% sous n_ctx."""
    n_ctx = CFG["model"]["n_ctx"]
    max_response = CFG["inference"]["max_tokens"]
    return int(n_ctx * 0.80) - max_response


# ---------------------------------------------------------------------------
# Singleton LLM
# ---------------------------------------------------------------------------

_LLM_INSTANCE = None


def _get_llm():
    global _LLM_INSTANCE
    if _LLM_INSTANCE is not None:
        return _LLM_INSTANCE

    try:
        from llama_cpp import Llama
    except ImportError:
        click.echo(
            "[erreur] llama-cpp-python non installé. "
            "Voir requirements.txt pour l'installation CUDA."
        )
        sys.exit(1)

    model_cfg = CFG["model"]
    model_path = Path(model_cfg["path"])
    if not model_path.exists():
        click.echo(f"[erreur] modèle GGUF introuvable : {model_path}")
        click.echo(
            "Télécharge le GGUF et mets à jour config.yaml."
        )
        sys.exit(1)

    click.echo(f"[init] chargement du modèle : {model_path.name}")
    _LLM_INSTANCE = Llama(
        model_path=str(model_path),
        n_gpu_layers=model_cfg["n_gpu_layers"],
        n_ctx=model_cfg["n_ctx"],
        n_batch=model_cfg["n_batch"],
        chat_format=model_cfg.get("chat_format"),
        seed=model_cfg.get("seed", -1),
        verbose=model_cfg.get("verbose", False),
    )
    click.echo("[init] modèle prêt")
    return _LLM_INSTANCE


def reset_llm() -> None:
    """Libère le singleton LLM (pour rechargement avec nouveaux paramètres)."""
    global _LLM_INSTANCE
    _LLM_INSTANCE = None


def llm_chat(
    system: str,
    user: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """Appel chat simple. Retourne le texte brut de la réponse."""
    llm = _get_llm()
    inf = CFG["inference"]
    resp = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature if temperature is not None else inf["temperature"],
        top_p=inf["top_p"],
        max_tokens=max_tokens if max_tokens is not None else inf["max_tokens"],
    )
    return resp["choices"][0]["message"]["content"].strip()


def llm_json(system: str, user: str, *, retries: int = 2) -> dict[str, Any]:
    """Appel LLM avec parsing JSON robuste. Retry si le parsing échoue."""
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        raw = llm_chat(
            system
            + "\n\nRéponds UNIQUEMENT avec un objet JSON valide, "
            "sans texte avant ni après, sans fences markdown.",
            user,
            temperature=0.1,
        )
        json_str = _extract_json_block(raw)
        if json_str is None:
            last_err = ValueError(
                f"Aucun bloc JSON détecté dans la réponse :\n{raw[:500]}"
            )
            if attempt < retries:
                user = (
                    user
                    + "\n\n[Ta réponse précédente ne contenait pas de JSON. "
                    "Réponds UNIQUEMENT avec un objet JSON, rien d'autre.]"
                )
            continue
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            last_err = e
            if attempt < retries:
                user = (
                    user
                    + f"\n\n[Ta réponse n'était pas du JSON valide : {e}. "
                    "Recommence, JSON uniquement.]"
                )
    raise RuntimeError(
        f"Échec du parsing JSON après {retries + 1} tentatives : {last_err}"
    )


def _extract_json_block(text: str) -> Optional[str]:
    """Extrait le premier bloc JSON équilibré du texte."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Utilitaires wiki
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"[^a-z0-9]+")
LINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def slugify(text: str) -> str:
    """Transforme un titre en slug ASCII kebab-case."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = SLUG_RE.sub("-", ascii_text).strip("-")
    return slug or "untitled"


def read_schema() -> str:
    if not SCHEMA_PATH.exists():
        return ""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def read_index(ctx: WikiContext = None) -> str:
    index_file = (ctx or DEFAULT_CTX).index_file
    if not index_file.exists():
        return ""
    return index_file.read_text(encoding="utf-8")


def append_log(op: str, title: str, body: str, ctx: WikiContext = None) -> None:
    log_file = (ctx or DEFAULT_CTX).log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"\n## [{timestamp}] {op} | {title}\n\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(header + body.rstrip() + "\n")


@dataclass
class WikiPage:
    path: Path
    meta: dict[str, Any]
    content: str

    @classmethod
    def load(cls, path: Path) -> "WikiPage":
        post = frontmatter.load(path)
        return cls(path=path, meta=dict(post.metadata), content=post.content)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(self.content, **self.meta)
        self.path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def list_wiki_pages(ctx: WikiContext = None) -> list[Path]:
    """Liste toutes les pages .md du wiki sauf index, log, overview."""
    c = ctx or DEFAULT_CTX
    if not c.wiki_dir.exists():
        return []
    excluded = {c.index_file.resolve(), c.log_file.resolve(), c.overview_file.resolve()}
    return [p for p in c.wiki_dir.rglob("*.md") if p.resolve() not in excluded]


# ---------------------------------------------------------------------------
# Chunking — résumé par passes pour les sources longues
# ---------------------------------------------------------------------------


def chunk_text(text: str, max_tokens: int = 3000) -> list[str]:
    """Découpe un texte en morceaux de ~max_tokens tokens (estimés).
    Coupe aux doubles retours à la ligne, puis aux simples retours,
    puis aux phrases en dernier recours."""
    if estimate_tokens(text) <= max_tokens:
        return [text]

    max_chars = max_tokens * 4

    # Essayer de découper aux doubles retours d'abord, puis simples
    paragraphs = re.split(r"\n{2,}", text)
    if len(paragraphs) < 2:
        paragraphs = re.split(r"\n", text)
    if len(paragraphs) < 2:
        # Dernier recours : couper aux phrases (. suivi d'espace)
        paragraphs = re.split(r"(?<=\. )", text)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    # Si on n'a toujours qu'un seul chunk (texte sans séparateurs),
    # forcer un découpage brutal par taille
    if len(chunks) == 1 and estimate_tokens(chunks[0]) > max_tokens:
        text = chunks[0]
        chunks = []
        for i in range(0, len(text), max_chars):
            chunks.append(text[i : i + max_chars])

    return chunks


SUMMARIZE_CHUNK_SYSTEM = (
    "Tu es un assistant qui résume des extraits de documents. "
    "Produis un résumé dense et fidèle du texte fourni, en conservant les noms "
    "propres, les dates, les chiffres clés et les idées principales. 300 mots max."
)


def summarize_long_source(text: str) -> str:
    """Si le texte dépasse le budget, résume par passes de chunks puis fusionne.
    Retourne le texte original s'il tient dans le budget."""
    budget = token_budget()
    content_budget = budget - 2500  # marge pour prompt extraction + index

    if estimate_tokens(text) <= content_budget:
        return text

    click.echo(
        f"[ingest] source longue ({estimate_tokens(text)} tokens estimés), "
        f"résumé par passes de chunks..."
    )

    chunks = chunk_text(text, max_tokens=min(3000, content_budget))
    summaries: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        click.echo(f"  chunk {i}/{len(chunks)}...")
        summary = llm_chat(
            system=SUMMARIZE_CHUNK_SYSTEM,
            user=f"Résume cet extrait (chunk {i}/{len(chunks)}) :\n\n{chunk}",
            max_tokens=800,
        )
        summaries.append(summary)

    combined = "\n\n---\n\n".join(summaries)

    if estimate_tokens(combined) > content_budget:
        click.echo("  passe de fusion...")
        combined = llm_chat(
            system=(
                "Fusionne ces résumés partiels en un seul résumé cohérent "
                "et complet. 500 mots max."
            ),
            user=combined,
            max_tokens=1200,
        )

    return combined


# ---------------------------------------------------------------------------
# Commande : ingest
# ---------------------------------------------------------------------------

INGEST_SYSTEM = (
    "Tu es un mainteneur de wiki rigoureux. Ton rôle est d'analyser une source "
    "et d'extraire une structure exploitable pour alimenter un wiki en markdown.\n\n"
    "Conventions :\n"
    "- Slugs en kebab-case ASCII, minuscules\n"
    "- Entités = noms propres ; concepts = idées/thèmes\n"
    "- Tags en minuscules kebab-case\n"
    "- Détecte les contradictions avec l'index existant"
)

INGEST_USER_TEMPLATE = """Voici l'index actuel du wiki (ce qui existe déjà) :

---INDEX---
{index}
---FIN INDEX---

Voici la source à ingérer (fichier : {source_path}) :

---SOURCE---
{source_content}
---FIN SOURCE---

Extrais au format JSON strict :

{{
  "title": "titre concis de la source",
  "slug": "slug-kebab-case",
  "summary_one_line": "une phrase résumant la source",
  "key_points": ["point 1", "point 2", "point 3"],
  "entities": [
    {{"name": "Nom", "slug": "nom", "kind": "person", "new": true, "note": "1-2 phrases"}}
  ],
  "concepts": [
    {{"name": "Nom", "slug": "nom", "new": true, "note": "1-2 phrases"}}
  ],
  "contradictions": [],
  "tags": ["tag1", "tag2"]
}}

"new" = true si l'entité/concept n'apparaît PAS dans l'index, false sinon.
"""

# -- Enrichissement de page existante --
ENRICH_SYSTEM = (
    "Tu es un mainteneur de wiki. On te donne le contenu actuel d'une page "
    "et de nouvelles informations issues d'une source fraîche.\n"
    "Produis une version mise à jour qui INTÈGRE les nouveautés SANS supprimer "
    "l'existant. Ajout incrémental, pas réécriture.\n\n"
    "Règles :\n"
    "- Conserve intégralement le contenu existant sauf si contredit.\n"
    "- Ajoute une section ou des paragraphes pour les infos nouvelles.\n"
    "- Si contradiction, mentionne les deux versions avec liens sources.\n"
    "- Markdown avec liens Obsidian [[slug]].\n"
    "- Ne modifie PAS le frontmatter, produis seulement le contenu markdown."
)

ENRICH_USER_TEMPLATE = """Page actuelle ({slug}) :

{current_content}

---

Nouvelles informations issues de [[{source_slug}]] :

{new_info}

---

Produis le contenu markdown mis à jour (sans frontmatter YAML).
"""


@click.group()
def cli() -> None:
    """LLM Wiki — CLI pour ingest, query, lint, overview."""


@cli.command()
@click.argument(
    "source_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--batch",
    is_flag=True,
    help="Mode batch : skip overview auto et interactions.",
)
def ingest(source_path: Path, batch: bool) -> None:
    """Ingère une source depuis raw/ et met à jour le wiki."""
    source_path = source_path.resolve()

    # Chemin relatif pour le frontmatter : relatif à raw/ si possible, sinon au projet
    try:
        source_rel = source_path.relative_to(RAW_DIR.resolve())
    except ValueError:
        try:
            source_rel = source_path.relative_to(PROJECT_ROOT)
        except ValueError:
            source_rel = source_path

    click.echo(f"[ingest] lecture de {source_rel}")
    raw_text = source_path.read_text(encoding="utf-8", errors="replace")

    # Résumé par chunks si trop long
    source_content = summarize_long_source(raw_text)

    index = read_index() or "(index vide)"

    click.echo("[ingest] extraction structurée via LLM...")
    data = llm_json(
        system=INGEST_SYSTEM,
        user=INGEST_USER_TEMPLATE.format(
            index=index,
            source_path=str(source_rel).replace("\\", "/"),
            source_content=source_content,
        ),
    )

    today = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(data.get("slug") or data.get("title", "source"))
    source_page_slug = f"{today}-{slug}"
    source_page_path = WIKI_DIR / "sources" / f"{source_page_slug}.md"

    # Éviter écrasement si slug dupliqué le même jour
    counter = 2
    while source_page_path.exists():
        source_page_slug = f"{today}-{slug}-{counter}"
        source_page_path = WIKI_DIR / "sources" / f"{source_page_slug}.md"
        counter += 1

    entities_links = [
        f"[[{slugify(e.get('slug') or e['name'])}]]"
        for e in data.get("entities", [])
    ]
    concepts_links = [
        f"[[{slugify(c.get('slug') or c['name'])}]]"
        for c in data.get("concepts", [])
    ]

    source_meta = {
        "type": "source",
        "title": data.get("title", "Sans titre"),
        "slug": slug,
        "ingested": today,
        "source_path": str(source_rel).replace("\\", "/"),
        "source_kind": "article",
        "tags": data.get("tags", []),
        "related_entities": entities_links,
        "related_concepts": concepts_links,
    }

    key_points_md = "\n".join(f"- {kp}" for kp in data.get("key_points", []))
    contradictions = data.get("contradictions") or []
    contradictions_md = ""
    if contradictions:
        items = "\n".join(f"- {c}" for c in contradictions)
        contradictions_md = f"\n\n## Contradictions détectées\n\n{items}"

    source_content_md = (
        f"# {data.get('title', 'Sans titre')}\n\n"
        f"{data.get('summary_one_line', '')}\n\n"
        f"## Points clés\n\n"
        f"{key_points_md}"
        f"{contradictions_md}\n\n"
        f"## Entités mentionnées\n\n"
        f"{', '.join(entities_links) if entities_links else '_aucune_'}\n\n"
        f"## Concepts mentionnés\n\n"
        f"{', '.join(concepts_links) if concepts_links else '_aucun_'}\n"
    )

    WikiPage(source_page_path, source_meta, source_content_md).save()
    click.echo(
        f"[ingest] page source créée : "
        f"{source_page_path.relative_to(PROJECT_ROOT)}"
    )

    # --- Entités : créer ou enrichir ---
    created_stubs: list[str] = []
    updated_stubs: list[str] = []

    for ent in data.get("entities", []):
        ent_slug = slugify(ent.get("slug") or ent["name"])
        ent_path = WIKI_DIR / "entities" / f"{ent_slug}.md"

        if ent.get("new") or not ent_path.exists():
            meta = {
                "type": "entity",
                "name": ent["name"],
                "slug": ent_slug,
                "kind": ent.get("kind", "other"),
                "aliases": [],
                "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today,
                "tags": data.get("tags", []),
            }
            content = f"# {ent['name']}\n\n{ent.get('note', '')}\n"
            WikiPage(ent_path, meta, content).save()
            created_stubs.append(f"[[{ent_slug}]] (entité)")
        else:
            _enrich_existing_page(
                ent_path,
                source_page_slug,
                ent.get("note", ""),
                today,
                updated_stubs,
                "entité",
            )

    for cpt in data.get("concepts", []):
        cpt_slug = slugify(cpt.get("slug") or cpt["name"])
        cpt_path = WIKI_DIR / "concepts" / f"{cpt_slug}.md"

        if cpt.get("new") or not cpt_path.exists():
            meta = {
                "type": "concept",
                "name": cpt["name"],
                "slug": cpt_slug,
                "aliases": [],
                "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today,
                "tags": data.get("tags", []),
            }
            content = f"# {cpt['name']}\n\n{cpt.get('note', '')}\n"
            WikiPage(cpt_path, meta, content).save()
            created_stubs.append(f"[[{cpt_slug}]] (concept)")
        else:
            _enrich_existing_page(
                cpt_path,
                source_page_slug,
                cpt.get("note", ""),
                today,
                updated_stubs,
                "concept",
            )

    # --- Index ---
    _update_index_entry(
        "Sources",
        f"- [[{source_page_slug}]] — "
        f"{data.get('summary_one_line', '')} _(ingéré {today})_",
    )
    for ent in data.get("entities", []):
        if ent.get("new"):
            es = slugify(ent.get("slug") or ent["name"])
            note_line = (ent.get("note", "") or ent["name"]).splitlines()[0]
            _update_index_entry("Entités", f"- [[{es}]] — {note_line}")
    for cpt in data.get("concepts", []):
        if cpt.get("new"):
            cs = slugify(cpt.get("slug") or cpt["name"])
            note_line = (cpt.get("note", "") or cpt["name"]).splitlines()[0]
            _update_index_entry("Concepts", f"- [[{cs}]] — {note_line}")

    # --- Log ---
    log_body = (
        f"- Source : `{source_rel}`\n"
        f"- Page créée : [[{source_page_slug}]]\n"
        f"- Stubs créés : "
        f"{', '.join(created_stubs) if created_stubs else '_aucun_'}\n"
        f"- Pages enrichies : "
        f"{', '.join(updated_stubs) if updated_stubs else '_aucune_'}\n"
        f"- Contradictions : {len(contradictions)}\n"
    )
    append_log("ingest", data.get("title", slug), log_body)

    # --- Rapport terminal ---
    click.echo("[ingest] terminé.")
    click.echo(f"  pages créées    : {1 + len(created_stubs)}")
    click.echo(f"  pages enrichies : {len(updated_stubs)}")
    if contradictions:
        click.echo(f"  /!\\ {len(contradictions)} contradiction(s) :")
        for c in contradictions:
            click.echo(f"      • {c}")

    # --- Overview auto (sauf mode batch) ---
    if not batch:
        click.echo("[ingest] mise à jour de overview.md...")
        _regenerate_overview()


def _enrich_existing_page(
    page_path: Path,
    source_page_slug: str,
    new_info: str,
    today: str,
    tracker: list[str],
    label: str,
) -> None:
    """Lit une page existante, demande au LLM de l'enrichir, sauvegarde."""
    page = WikiPage.load(page_path)
    page_slug = page_path.stem

    # Ajouter la source au frontmatter
    src_ref = f"[[{source_page_slug}]]"
    sources = page.meta.get("sources", []) or []
    if src_ref not in sources:
        sources.append(src_ref)
        page.meta["sources"] = sources

    # Si pas d'info nouvelle substantielle → frontmatter seul
    if not new_info or len(new_info.strip()) < 20:
        page.meta["last_updated"] = today
        page.save()
        tracker.append(f"[[{page_slug}]] ({label}, frontmatter)")
        return

    # Enrichissement via LLM
    try:
        updated_content = llm_chat(
            system=ENRICH_SYSTEM,
            user=ENRICH_USER_TEMPLATE.format(
                slug=page_slug,
                current_content=page.content,
                source_slug=source_page_slug,
                new_info=new_info,
            ),
            max_tokens=1500,
        )

        # Sécurité : version enrichie ne doit pas être trop courte
        if len(updated_content) >= len(page.content) * 0.7:
            page.content = updated_content
        else:
            click.echo(
                f"  [warn] enrichissement [[{page_slug}]] rejeté "
                f"({len(updated_content)} vs {len(page.content)} chars). "
                f"Frontmatter mis à jour uniquement."
            )
    except Exception as e:
        click.echo(
            f"  [warn] enrichissement [[{page_slug}]] échoué : {e}"
        )

    page.meta["last_updated"] = today
    page.save()
    tracker.append(f"[[{page_slug}]] ({label})")


def _update_index_entry(section: str, line: str, ctx: WikiContext = None) -> None:
    """Ajoute une ligne sous la section donnée de index.md."""
    index_file = (ctx or DEFAULT_CTX).index_file
    index_file.parent.mkdir(parents=True, exist_ok=True)
    if not index_file.exists():
        index_file.write_text("# Index du wiki\n\n", encoding="utf-8")

    text = index_file.read_text(encoding="utf-8")
    section_header = f"## {section}"

    # Retirer le placeholder "_Aucun(e)..."
    text = re.sub(
        rf"({re.escape(section_header)}\n\n)_Aucun[^\n]*_\n",
        rf"\1",
        text,
    )

    if section_header not in text:
        text = text.rstrip() + f"\n\n{section_header}\n\n{line}\n"
    else:
        lines_list = text.splitlines()
        out: list[str] = []
        inserted = False
        in_section = False
        for ln in lines_list:
            if ln.strip() == section_header:
                in_section = True
                out.append(ln)
                continue
            if in_section and ln.startswith("## "):
                out.append(line)
                out.append("")
                inserted = True
                in_section = False
            out.append(ln)
        if not inserted:
            if out and out[-1].strip() != "":
                out.append("")
            out.append(line)
        text = "\n".join(out) + "\n"

    # Dédoublonne lignes identiques consécutives
    text = re.sub(r"(?m)^(- \[\[[^\]]+\]\].*)\n\1\n", r"\1\n", text)
    index_file.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Commande : query
# ---------------------------------------------------------------------------

QUERY_SELECT_SYSTEM = (
    "Tu es un assistant de recherche. Étant donné l'index d'un wiki et une "
    "question, sélectionne les 3 à 8 pages les plus pertinentes.\n"
    "Réponds en JSON strict avec les slugs tels qu'ils apparaissent dans l'index."
)

QUERY_SELECT_USER = """Index :

{index}

Question : {question}

JSON attendu :
{{
  "pages": ["slug-1", "slug-2"],
  "reasoning": "explication courte"
}}
"""

QUERY_ANSWER_SYSTEM = (
    "Tu es un assistant qui répond en s'appuyant STRICTEMENT sur les pages wiki "
    "fournies. Si l'information n'est pas dans les pages, dis-le. "
    "Cite avec des liens [[slug]]. Pas d'invention ni d'extrapolation."
)

QUERY_ANSWER_USER = """Question : {question}

Pages :

{pages}

Réponds en markdown concis avec des liens [[page]].
"""


@cli.command()
@click.argument("question")
@click.option(
    "--file-back",
    is_flag=True,
    help="Sauvegarde la réponse comme page wiki.",
)
def query(question: str, file_back: bool) -> None:
    """Pose une question au wiki."""
    index = read_index()
    if not index.strip():
        click.echo(
            "[query] index vide — commence par ingérer des sources."
        )
        sys.exit(0)

    click.echo("[query] sélection des pages pertinentes...")
    selection = llm_json(
        system=QUERY_SELECT_SYSTEM,
        user=QUERY_SELECT_USER.format(index=index, question=question),
    )

    selected_slugs = selection.get("pages", [])
    if not selected_slugs:
        click.echo("[query] aucune page pertinente identifiée.")
        click.echo(f"  raisonnement : {selection.get('reasoning', '')}")
        return

    all_pages = list_wiki_pages()
    slug_to_path = {p.stem: p for p in all_pages}

    loaded: list[tuple[str, str]] = []
    missing: list[str] = []

    budget = token_budget() - estimate_tokens(QUERY_ANSWER_SYSTEM) - 200
    tokens_used = 0

    for raw_slug in selected_slugs:
        clean = raw_slug.strip().strip("[]")
        p = slug_to_path.get(clean)
        if p is None:
            missing.append(clean)
            continue
        content = p.read_text(encoding="utf-8")
        content_tokens = estimate_tokens(content)
        if tokens_used + content_tokens > budget:
            click.echo(
                f"  [budget] [[{clean}]] ignorée "
                f"({content_tokens} tok, budget {tokens_used}/{budget})"
            )
            continue
        tokens_used += content_tokens
        loaded.append((clean, content))

    if missing:
        click.echo(f"[query] pages introuvables : {missing}")
    if not loaded:
        click.echo("[query] aucune page chargeable.")
        return

    pages_blob = "\n\n---\n\n".join(
        f"### [[{slug}]]\n\n{content}" for slug, content in loaded
    )

    click.echo(
        f"[query] synthèse à partir de {len(loaded)} page(s) "
        f"({tokens_used} tokens)..."
    )
    answer = llm_chat(
        system=QUERY_ANSWER_SYSTEM,
        user=QUERY_ANSWER_USER.format(question=question, pages=pages_blob),
    )

    click.echo("\n" + "=" * 60)
    click.echo(answer)
    click.echo("=" * 60)

    if file_back:
        _file_back_answer(question, answer)
    else:
        click.echo(
            "\n  Tip : relancer avec --file-back pour sauvegarder "
            "cette réponse dans le wiki."
        )

    append_log(
        "query",
        question[:80],
        f"- Pages consultées : "
        f"{', '.join(f'[[{s}]]' for s, _ in loaded)}\n"
        f"- File-back : {'oui' if file_back else 'non'}\n",
    )


def _file_back_answer(question: str, answer: str) -> None:
    """Sauvegarde une réponse de query comme page wiki."""
    today = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(question[:60])
    slug_final = slug
    page_path = WIKI_DIR / "concepts" / f"{slug_final}.md"

    counter = 2
    while page_path.exists():
        slug_final = f"{slug}-{counter}"
        page_path = WIKI_DIR / "concepts" / f"{slug_final}.md"
        counter += 1

    # Extraire les [[liens]] de la réponse pour les lister comme sources
    refs = LINK_RE.findall(answer)

    meta = {
        "type": "concept",
        "name": question,
        "slug": slug_final,
        "aliases": [],
        "sources": [f"[[{r}]]" for r in refs],
        "origin_query": question,
        "origin_date": today,
        "last_updated": today,
        "tags": ["file-back"],
    }

    content = f"# {question}\n\n{answer}\n"
    WikiPage(page_path, meta, content).save()

    _update_index_entry(
        "Concepts",
        f"- [[{slug_final}]] — file-back query _(créé {today})_",
    )
    click.echo(
        f"\n[file-back] page créée : {page_path.relative_to(PROJECT_ROOT)}"
    )


# ---------------------------------------------------------------------------
# Commande : overview
# ---------------------------------------------------------------------------

OVERVIEW_SYSTEM = (
    "Tu es un mainteneur de wiki. Produis une page de synthèse (overview) :\n"
    "1. Résumé en 3-5 phrases du périmètre couvert.\n"
    "2. Thèmes principaux avec liens [[slug]].\n"
    "3. Contradictions ou tensions connues.\n"
    "4. Moins de 400 mots.\n"
    "Markdown avec liens Obsidian [[slug]]."
)


@cli.command()
def overview() -> None:
    """Régénère la page de synthèse overview.md."""
    _regenerate_overview()


def _regenerate_overview() -> None:
    index = read_index()
    if not index.strip() or "## Sources" not in index:
        click.echo(
            "[overview] pas assez de contenu pour une overview."
        )
        return

    text = llm_chat(
        system=OVERVIEW_SYSTEM,
        user=f"Index actuel :\n\n{index}",
        max_tokens=1200,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    meta = {
        "type": "concept",
        "name": "Vue d'ensemble",
        "slug": "overview",
        "last_updated": today,
        "tags": ["meta"],
    }
    content = f"# Vue d'ensemble du wiki\n\n{text}\n"
    WikiPage(OVERVIEW_FILE, meta, content).save()
    click.echo(
        f"[overview] page mise à jour : "
        f"{OVERVIEW_FILE.relative_to(PROJECT_ROOT)}"
    )
    append_log("note", "overview régénéré", f"- Mis à jour le {today}.\n")


# ---------------------------------------------------------------------------
# Commande : lint
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--fix-index",
    is_flag=True,
    help="Corrige l'index désynchronisé automatiquement.",
)
@click.option(
    "--auto-fix",
    is_flag=True,
    help="Corrige index + frontmatter invalide + suggère des fixes pour liens cassés.",
)
def lint(fix_index: bool, auto_fix: bool) -> None:
    """Vérifie la cohérence du wiki (liens, frontmatter, index)."""
    if auto_fix:
        fix_index = True  # --auto-fix implique --fix-index
    pages = list_wiki_pages()
    if not pages:
        click.echo("[lint] wiki vide.")
        return

    click.echo(f"[lint] analyse de {len(pages)} page(s)...\n")

    known_slugs = {p.stem for p in pages}
    broken_links: list[tuple[str, str]] = []
    invalid_frontmatter: list[tuple[str, str]] = []
    inbound_count: dict[str, int] = {s: 0 for s in known_slugs}
    total_tokens = 0

    for page_path in pages:
        rel = page_path.relative_to(PROJECT_ROOT)
        raw_content = page_path.read_text(encoding="utf-8", errors="replace")
        total_tokens += estimate_tokens(raw_content)

        try:
            post = frontmatter.load(page_path)
        except Exception as e:
            invalid_frontmatter.append((str(rel), f"parse error: {e}"))
            continue

        meta = post.metadata
        if not meta:
            invalid_frontmatter.append((str(rel), "frontmatter absent"))
        else:
            ptype = meta.get("type")
            if ptype not in {"source", "entity", "concept"}:
                invalid_frontmatter.append(
                    (str(rel), f"type invalide: {ptype!r}")
                )
            required_fields = {
                "source": ["title", "ingested"],
                "entity": ["name"],
                "concept": ["name"],
            }
            for field_name in required_fields.get(ptype, []):
                if field_name not in meta:
                    invalid_frontmatter.append(
                        (str(rel), f"champ manquant: {field_name}")
                    )

        # Compter les liens (dans contenu + frontmatter stringifié)
        full_text = frontmatter.dumps(post)
        for match in LINK_RE.finditer(full_text):
            target = match.group(1).strip()
            if target not in known_slugs:
                broken_links.append((str(rel), target))
            else:
                inbound_count[target] += 1

    orphans = [
        s
        for s, count in inbound_count.items()
        if count == 0
        and not any(
            p.stem == s and "sources" in p.parts for p in pages
        )
    ]

    index_text = read_index()
    index_slugs = set(LINK_RE.findall(index_text))
    in_wiki_not_index = known_slugs - index_slugs
    in_index_not_wiki = index_slugs - known_slugs

    # --- Rapport ---
    click.echo(f"Pages analysées            : {len(pages)}")
    click.echo(f"Tokens totaux (estimé)     : {total_tokens:,}")
    click.echo(f"Liens cassés               : {len(broken_links)}")
    click.echo(f"Frontmatter invalide       : {len(invalid_frontmatter)}")
    click.echo(f"Pages orphelines           : {len(orphans)}")
    click.echo(f"Absentes de l'index        : {len(in_wiki_not_index)}")
    click.echo(
        f"Fantômes dans l'index      : {len(in_index_not_wiki)}\n"
    )

    if broken_links:
        click.echo("## Liens cassés\n")
        fixes_applied = 0
        for page, target in broken_links:
            suggestions = _fuzzy_match(target, list(known_slugs), n=1)
            if suggestions and auto_fix:
                best = suggestions[0]
                click.echo(f"  {page} -> [[{target}]]  [fix -> [[{best}]]]")
                # Appliquer le fix dans le fichier
                page_path = PROJECT_ROOT / page
                try:
                    content = page_path.read_text(encoding="utf-8")
                    content = content.replace(f"[[{target}]]", f"[[{best}]]")
                    page_path.write_text(content, encoding="utf-8")
                    fixes_applied += 1
                except Exception as e:
                    click.echo(f"    [erreur] impossible de corriger : {e}")
            elif suggestions:
                click.echo(
                    f"  {page} -> [[{target}]]"
                    f"  (suggestion: [[{suggestions[0]}]])"
                )
            else:
                click.echo(f"  {page} -> [[{target}]]")
        if auto_fix and fixes_applied:
            click.echo(f"\n  [fix] {fixes_applied} lien(s) corrigé(s).")
        click.echo()

    if invalid_frontmatter:
        click.echo("## Frontmatter invalide\n")
        fm_fixes = 0
        for page, reason in invalid_frontmatter:
            click.echo(f"  {page} : {reason}")
            if auto_fix and "champ manquant" in reason:
                # Tenter de corriger les champs manquants avec des valeurs par défaut
                page_path = PROJECT_ROOT / page
                try:
                    post = frontmatter.load(page_path)
                    field = reason.split("champ manquant: ")[1]
                    defaults = {
                        "title": page_path.stem.replace("-", " ").title(),
                        "name": page_path.stem.replace("-", " ").title(),
                        "ingested": datetime.now().strftime("%Y-%m-%d"),
                        "slug": page_path.stem,
                    }
                    if field in defaults:
                        post.metadata[field] = defaults[field]
                        wp = WikiPage(
                            page_path,
                            dict(post.metadata),
                            post.content,
                        )
                        wp.save()
                        click.echo(f"    [fix] {field} = {defaults[field]!r}")
                        fm_fixes += 1
                except Exception as e:
                    click.echo(f"    [erreur] {e}")
        if auto_fix and fm_fixes:
            click.echo(f"\n  [fix] {fm_fixes} frontmatter(s) corrigé(s).")
        click.echo()

    if orphans:
        click.echo("## Pages orphelines\n")
        for s in orphans:
            click.echo(f"  [[{s}]]")
        click.echo()

    if in_wiki_not_index:
        click.echo("## Pages absentes de index.md\n")
        for s in sorted(in_wiki_not_index):
            click.echo(f"  [[{s}]]")
        if fix_index:
            click.echo("\n  [fix] ajout à l'index...")
            for s in sorted(in_wiki_not_index):
                p = next((pg for pg in pages if pg.stem == s), None)
                if p is None:
                    continue
                if "entities" in p.parts:
                    _update_index_entry(
                        "Entités", f"- [[{s}]] — (ajouté par lint)"
                    )
                elif "concepts" in p.parts:
                    _update_index_entry(
                        "Concepts", f"- [[{s}]] — (ajouté par lint)"
                    )
                elif "sources" in p.parts:
                    _update_index_entry(
                        "Sources", f"- [[{s}]] — (ajouté par lint)"
                    )
            click.echo("  [fix] index corrigé.")
        click.echo()

    if in_index_not_wiki:
        click.echo(
            "## Fantômes dans l'index (référencés mais inexistants)\n"
        )
        for s in sorted(in_index_not_wiki):
            click.echo(f"  [[{s}]]")
        click.echo()

    total_issues = (
        len(broken_links)
        + len(invalid_frontmatter)
        + len(orphans)
        + len(in_wiki_not_index)
        + len(in_index_not_wiki)
    )
    if total_issues == 0:
        click.echo("  Aucun problème détecté.")

    append_log(
        "lint",
        "health check",
        f"- Pages: {len(pages)} | tokens: {total_tokens:,} | "
        f"liens cassés: {len(broken_links)} | "
        f"frontmatter: {len(invalid_frontmatter)} | "
        f"orphelines: {len(orphans)} | "
        f"désync index: {len(in_wiki_not_index) + len(in_index_not_wiki)}\n",
    )


# ---------------------------------------------------------------------------
# Commande : search — recherche full-text locale (BM25 simplifié)
# ---------------------------------------------------------------------------

import math
from collections import Counter


def _tokenize_simple(text: str) -> list[str]:
    """Tokenisation minimaliste : minuscules, split sur non-alphanum, filtrage."""
    return [w for w in re.split(r"[^a-zà-ÿ0-9]+", text.lower()) if len(w) > 1]


def _build_bm25_index(
    pages: list[Path],
) -> tuple[list[tuple[str, Path, str]], dict[str, float]]:
    """Construit un index BM25 léger en mémoire.
    Retourne (documents, idf) où documents = [(slug, path, raw_text), ...]."""
    docs: list[tuple[str, Path, str]] = []
    df: Counter[str] = Counter()

    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        docs.append((p.stem, p, text))
        terms = set(_tokenize_simple(text))
        for t in terms:
            df[t] += 1

    n = len(docs)
    idf = {
        term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
        for term, freq in df.items()
    }
    return docs, idf


def _bm25_score(
    query_terms: list[str],
    doc_text: str,
    idf: dict[str, float],
    avg_dl: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Score BM25 d'un document pour une requête."""
    doc_terms = _tokenize_simple(doc_text)
    dl = len(doc_terms)
    tf = Counter(doc_terms)
    score = 0.0
    for qt in query_terms:
        if qt not in idf:
            continue
        f = tf.get(qt, 0)
        numerator = f * (k1 + 1)
        denominator = f + k1 * (1 - b + b * dl / max(avg_dl, 1))
        score += idf[qt] * numerator / denominator
    return score


@cli.command()
@click.argument("terms", nargs=-1, required=True)
@click.option("-n", "--top", default=10, help="Nombre de résultats.")
def search(terms: tuple[str, ...], top: int) -> None:
    """Recherche full-text dans les pages wiki (BM25)."""
    pages = list_wiki_pages()
    if not pages:
        click.echo("[search] wiki vide.")
        return

    query_str = " ".join(terms)
    query_terms = _tokenize_simple(query_str)
    if not query_terms:
        click.echo("[search] aucun terme valide dans la requête.")
        return

    docs, idf = _build_bm25_index(pages)
    avg_dl = sum(len(_tokenize_simple(d[2])) for d in docs) / len(docs)

    scored: list[tuple[float, str, Path]] = []
    for slug, path, text in docs:
        s = _bm25_score(query_terms, text, idf, avg_dl)
        if s > 0:
            scored.append((s, slug, path))

    scored.sort(reverse=True)

    if not scored:
        click.echo(f"[search] aucun résultat pour : {query_str}")
        return

    click.echo(f"[search] {len(scored)} résultat(s) pour « {query_str} »\n")
    for rank, (score, slug, path) in enumerate(scored[:top], 1):
        rel = path.relative_to(PROJECT_ROOT)
        # Extrait un snippet autour du premier terme trouvé
        snippet = _extract_snippet(
            path.read_text(encoding="utf-8", errors="replace"),
            query_terms,
        )
        click.echo(f"  {rank:2d}. [[{slug}]]  (score: {score:.2f})")
        click.echo(f"      {rel}")
        if snippet:
            click.echo(f"      ...{snippet}...")
        click.echo()


def _extract_snippet(
    text: str, query_terms: list[str], context: int = 100
) -> str:
    """Extrait un snippet de ~context chars autour du premier terme trouvé.
    Ignore le frontmatter YAML."""
    # Retirer le frontmatter
    body = text
    if body.startswith("---"):
        end_fm = body.find("---", 3)
        if end_fm != -1:
            body = body[end_fm + 3 :].strip()

    body_lower = body.lower()
    best_pos = -1
    for qt in query_terms:
        pos = body_lower.find(qt)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos

    if best_pos == -1:
        return ""

    start = max(0, best_pos - context // 2)
    end = min(len(body), best_pos + context // 2)
    snippet = body[start:end].replace("\n", " ").strip()
    return snippet


# ---------------------------------------------------------------------------
# Commande : stats — dashboard rapide du wiki
# ---------------------------------------------------------------------------


@cli.command()
def stats() -> None:
    """Affiche des statistiques sur l'état du wiki."""
    pages = list_wiki_pages()

    sources = [p for p in pages if "sources" in p.parts]
    entities = [p for p in pages if "entities" in p.parts]
    concepts = [p for p in pages if "concepts" in p.parts]
    other = [
        p
        for p in pages
        if "sources" not in p.parts
        and "entities" not in p.parts
        and "concepts" not in p.parts
    ]

    total_tokens = 0
    total_chars = 0
    tags_counter: Counter[str] = Counter()
    kinds_counter: Counter[str] = Counter()

    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        total_chars += len(text)
        total_tokens += estimate_tokens(text)
        try:
            post = frontmatter.load(p)
            for tag in post.metadata.get("tags", []):
                tags_counter[tag] += 1
            kind = post.metadata.get("kind")
            if kind:
                kinds_counter[kind] += 1
        except Exception:
            pass

    # Sources brutes
    raw_files = list(RAW_DIR.rglob("*"))
    raw_files = [f for f in raw_files if f.is_file()]

    click.echo("=" * 50)
    click.echo("  LLM Wiki — Statistiques")
    click.echo("=" * 50)
    click.echo()
    click.echo(f"  Sources brutes (raw/)     : {len(raw_files)} fichier(s)")
    click.echo()
    click.echo(f"  Pages wiki total          : {len(pages)}")
    click.echo(f"    sources/                : {len(sources)}")
    click.echo(f"    entities/               : {len(entities)}")
    click.echo(f"    concepts/               : {len(concepts)}")
    if other:
        click.echo(f"    autres                  : {len(other)}")
    click.echo()
    click.echo(f"  Taille totale             : {total_chars:,} caractères")
    click.echo(f"  Tokens estimés            : {total_tokens:,}")
    click.echo(
        f"  Budget contexte           : {token_budget():,} tokens "
        f"(n_ctx={CFG['model']['n_ctx']})"
    )
    pct = (total_tokens / token_budget() * 100) if token_budget() > 0 else 0
    click.echo(
        f"  Ratio wiki/contexte       : {pct:.0f}% "
        f"({'index suffit' if pct < 500 else 'recherche recommandée'})"
    )
    click.echo()

    if tags_counter:
        click.echo("  Tags les plus fréquents :")
        for tag, count in tags_counter.most_common(10):
            click.echo(f"    {tag:30s} {count}")
        click.echo()

    if kinds_counter:
        click.echo("  Types d'entités :")
        for kind, count in kinds_counter.most_common():
            click.echo(f"    {kind:30s} {count}")
        click.echo()

    # Log : dernières opérations
    if LOG_FILE.exists():
        log_text = LOG_FILE.read_text(encoding="utf-8")
        entries = re.findall(r"^## \[.+$", log_text, re.MULTILINE)
        click.echo(f"  Entrées dans le log       : {len(entries)}")
        if entries:
            click.echo(f"  Dernière opération        : {entries[-1]}")
    click.echo()


# ---------------------------------------------------------------------------
# Commande : batch-ingest — ingérer tout un dossier
# ---------------------------------------------------------------------------


@cli.command("batch-ingest")
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    required=False,
)
@click.option("--dry-run", is_flag=True, help="Lister les fichiers sans ingérer.")
@click.option(
    "--ext",
    default=".md,.txt,.html",
    help="Extensions à ingérer, séparées par des virgules.",
)
def batch_ingest(directory: Optional[Path], dry_run: bool, ext: str) -> None:
    """Ingère tous les fichiers d'un dossier (défaut: raw/)."""
    target = (directory or RAW_DIR).resolve()
    extensions = {e.strip().lower() for e in ext.split(",")}
    # Ajouter le point si absent
    extensions = {e if e.startswith(".") else f".{e}" for e in extensions}

    # Trouver les fichiers candidats
    candidates: list[Path] = []
    for f in sorted(target.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            candidates.append(f)

    if not candidates:
        click.echo(f"[batch-ingest] aucun fichier trouvé dans {target} (extensions: {extensions})")
        return

    # Filtrer ceux déjà ingérés (source_path dans le log ou les pages sources)
    already_ingested: set[str] = set()
    sources_dir = WIKI_DIR / "sources"
    if sources_dir.exists():
        for sp in sources_dir.glob("*.md"):
            try:
                post = frontmatter.load(sp)
                src = post.metadata.get("source_path", "")
                if src:
                    already_ingested.add(src)
            except Exception:
                pass

    to_ingest: list[Path] = []
    skipped: list[Path] = []
    for f in candidates:
        try:
            rel = str(f.relative_to(RAW_DIR.resolve())).replace("\\", "/")
        except ValueError:
            rel = str(f).replace("\\", "/")
        if rel in already_ingested:
            skipped.append(f)
        else:
            to_ingest.append(f)

    click.echo(f"[batch-ingest] {len(candidates)} fichier(s) trouvé(s)")
    click.echo(f"  à ingérer : {len(to_ingest)}")
    click.echo(f"  déjà faits: {len(skipped)}")
    click.echo()

    if dry_run:
        click.echo("[dry-run] fichiers à ingérer :")
        for f in to_ingest:
            click.echo(f"  {f.relative_to(PROJECT_ROOT)}")
        return

    # Ingérer séquentiellement en mode batch
    success = 0
    errors: list[tuple[Path, str]] = []
    for i, f in enumerate(to_ingest, 1):
        click.echo(f"\n{'='*50}")
        click.echo(f"[batch-ingest] {i}/{len(to_ingest)} : {f.name}")
        click.echo(f"{'='*50}")
        try:
            cli.main(["ingest", str(f), "--batch"], standalone_mode=False)
            success += 1
        except Exception as e:
            click.echo(f"  [erreur] {e}")
            errors.append((f, str(e)))

    click.echo(f"\n{'='*50}")
    click.echo(f"[batch-ingest] terminé : {success} réussi(s), {len(errors)} erreur(s)")
    if errors:
        click.echo("\nErreurs :")
        for f, err in errors:
            click.echo(f"  {f.name} : {err}")


# ---------------------------------------------------------------------------
# Commande : export — exporter une page en HTML ou Marp
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 800px; margin: 2rem auto; padding: 0 1rem;
    line-height: 1.6; color: #333;
  }}
  h1 {{ border-bottom: 2px solid #4a9eff; padding-bottom: 0.5rem; }}
  h2 {{ color: #4a9eff; margin-top: 2rem; }}
  a {{ color: #4a9eff; }}
  code {{ background: #f4f4f4; padding: 0.2em 0.4em; border-radius: 3px; }}
  pre {{ background: #f4f4f4; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
  blockquote {{ border-left: 3px solid #4a9eff; margin-left: 0; padding-left: 1rem; color: #666; }}
  .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 2rem; }}
  .meta span {{ margin-right: 1.5rem; }}
</style>
</head>
<body>
<div class="meta">
{meta_html}
</div>
{body_html}
<footer style="margin-top:3rem; border-top:1px solid #eee; padding-top:1rem; color:#aaa; font-size:0.8rem;">
Exporté depuis LLM Wiki le {date}
</footer>
</body>
</html>
"""

MARP_TEMPLATE = """\
---
marp: true
theme: default
paginate: true
---

# {title}

{date}

---

{slides}
"""


def _md_to_html_basic(md_text: str) -> str:
    """Conversion markdown → HTML très basique (sans dépendance externe).
    Gère les headers, bold, italic, liens wiki, listes, code blocks."""
    lines = md_text.split("\n")
    html_lines: list[str] = []
    in_code = False
    in_list = False

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                lang = line.strip()[3:].strip()
                html_lines.append(f'<pre><code class="{lang}">')
                in_code = True
            continue
        if in_code:
            import html as html_module
            html_lines.append(html_module.escape(line))
            continue

        # Fermer la liste si on sort
        if in_list and not line.strip().startswith("- "):
            html_lines.append("</ul>")
            in_list = False

        # Headers
        if line.startswith("# "):
            html_lines.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{_inline_md(line[4:])}</h3>")
        # Lists
        elif line.strip().startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_md(line.strip()[2:])}</li>")
        # Empty line
        elif not line.strip():
            html_lines.append("")
        # Paragraph
        else:
            html_lines.append(f"<p>{_inline_md(line)}</p>")

    if in_list:
        html_lines.append("</ul>")
    if in_code:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """Convertit bold, italic, liens wiki, et code inline."""
    # Code inline
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Liens wiki [[slug|texte]] ou [[slug]]
    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]",
        r'<a href="\1.html">\2</a>',
        text,
    )
    text = re.sub(
        r"\[\[([^\]]+)\]\]",
        r'<a href="\1.html">\1</a>',
        text,
    )
    return text


def _md_to_marp_slides(md_text: str) -> str:
    """Découpe du markdown en slides Marp (séparation aux H2)."""
    slides: list[str] = []
    current: list[str] = []

    for line in md_text.split("\n"):
        if line.startswith("## ") and current:
            slides.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        slides.append("\n".join(current))

    return "\n\n---\n\n".join(slides)


@cli.command()
@click.argument("slug")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["html", "marp"]),
    default="html",
    help="Format d'export.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Chemin du fichier de sortie.",
)
def export(slug: str, fmt: str, output: Optional[Path]) -> None:
    """Exporte une page wiki en HTML standalone ou en slides Marp."""
    # Résoudre le slug
    pages = list_wiki_pages()
    slug_to_path = {p.stem: p for p in pages}
    path = slug_to_path.get(slug)

    if path is None:
        click.echo(f"[export] page [[{slug}]] introuvable.")
        # Suggestion fuzzy
        suggestions = _fuzzy_match(slug, list(slug_to_path.keys()), n=3)
        if suggestions:
            click.echo(f"  Suggestions : {', '.join(f'[[{s}]]' for s in suggestions)}")
        return

    page = WikiPage.load(path)
    today = datetime.now().strftime("%Y-%m-%d")

    if fmt == "html":
        # Construire les métadonnées HTML
        meta_parts: list[str] = []
        if page.meta.get("type"):
            meta_parts.append(f"<span>Type : {page.meta['type']}</span>")
        tags = page.meta.get("tags", [])
        if tags:
            meta_parts.append(f"<span>Tags : {', '.join(tags)}</span>")
        if page.meta.get("last_updated"):
            meta_parts.append(f"<span>Dernière MàJ : {page.meta['last_updated']}</span>")
        meta_html = "\n".join(meta_parts)

        body_html = _md_to_html_basic(page.content)
        title = page.meta.get("title") or page.meta.get("name") or slug

        html = HTML_TEMPLATE.format(
            title=title,
            meta_html=meta_html,
            body_html=body_html,
            date=today,
        )

        out_path = output or (PROJECT_ROOT / f"{slug}.html")
        out_path.write_text(html, encoding="utf-8")
        click.echo(f"[export] HTML exporté : {out_path}")

    elif fmt == "marp":
        title = page.meta.get("title") or page.meta.get("name") or slug
        slides = _md_to_marp_slides(page.content)
        marp = MARP_TEMPLATE.format(title=title, date=today, slides=slides)

        out_path = output or (PROJECT_ROOT / f"{slug}.marp.md")
        out_path.write_text(marp, encoding="utf-8")
        click.echo(f"[export] Marp exporté : {out_path}")
        click.echo("  Pour convertir en PDF/HTML : marp --html {out_path}")


# ---------------------------------------------------------------------------
# Fuzzy match pour suggestions (utilisé par lint et export)
# ---------------------------------------------------------------------------


def _fuzzy_match(query: str, candidates: list[str], n: int = 3) -> list[str]:
    """Retourne les n candidats les plus proches du query par distance d'édition simplifiée."""
    scored: list[tuple[float, str]] = []
    q_set = set(query.lower().split("-"))
    for c in candidates:
        c_set = set(c.lower().split("-"))
        # Jaccard sur les segments kebab-case
        if not q_set or not c_set:
            continue
        inter = len(q_set & c_set)
        union = len(q_set | c_set)
        score = inter / union if union > 0 else 0
        # Bonus si le début match
        if c.lower().startswith(query.lower()[:3]):
            score += 0.3
        if score > 0.1:
            scored.append((score, c))
    scored.sort(reverse=True)
    return [c for _, c in scored[:n]]


# ---------------------------------------------------------------------------
# Commande : watch — surveille raw/ et ingère automatiquement
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--interval", default=5, help="Intervalle de scan en secondes.")
@click.option("--ext", default=".md,.txt,.html", help="Extensions à surveiller.")
def watch(interval: int, ext: str) -> None:
    """Surveille raw/ et ingère automatiquement les nouveaux fichiers."""
    import time

    extensions = {e.strip() if e.startswith(".") else f".{e}" for e in ext.split(",")}

    click.echo(f"[watch] surveillance de {RAW_DIR}")
    click.echo(f"  extensions : {extensions}")
    click.echo(f"  intervalle : {interval}s")
    click.echo(f"  Ctrl+C pour arrêter.\n")

    _get_llm()  # Pré-charger une seule fois

    def _already_ingested() -> set[str]:
        done: set[str] = set()
        sources_dir = WIKI_DIR / "sources"
        if sources_dir.exists():
            for sp in sources_dir.glob("*.md"):
                try:
                    post = frontmatter.load(sp)
                    src = post.metadata.get("source_path", "")
                    if src:
                        done.add(src)
                except Exception:
                    pass
        return done

    seen = _already_ingested()
    click.echo(f"  {len(seen)} source(s) déjà ingérée(s).")

    try:
        while True:
            for f in sorted(RAW_DIR.rglob("*")):
                if not f.is_file() or f.suffix.lower() not in extensions:
                    continue
                try:
                    rel = str(f.relative_to(RAW_DIR.resolve())).replace("\\", "/")
                except ValueError:
                    continue
                if rel in seen:
                    continue

                click.echo(f"\n[watch] nouveau fichier : {rel}")
                try:
                    cli.main(["ingest", str(f), "--batch"], standalone_mode=False)
                except SystemExit:
                    pass
                except Exception as e:
                    click.echo(f"  [erreur] {e}")
                seen.add(rel)

            time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("\n[watch] arrêté.")


# ---------------------------------------------------------------------------
# Commande : graph — graphe interactif HTML (d3.js)
# ---------------------------------------------------------------------------

GRAPH_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>LLM Wiki — Graphe</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:system-ui,sans-serif; background:#0d1117; color:#c9d1d9; overflow:hidden; }}
svg {{ width:100vw; height:100vh; }}
.node {{ cursor:pointer; }}
.node circle {{ stroke-width:2px; }}
.node text {{ font-size:11px; fill:#c9d1d9; pointer-events:none; }}
.link {{ stroke-opacity:0.35; }}
.tip {{
  position:absolute; background:#161b22; border:1px solid #30363d;
  border-radius:6px; padding:8px 12px; font-size:12px;
  pointer-events:none; display:none; max-width:280px;
}}
.tip b {{ color:#58a6ff; }}
.legend,.info {{
  position:absolute; background:#161b22; border:1px solid #30363d;
  border-radius:6px; padding:10px 14px; font-size:12px;
}}
.legend {{ top:12px; right:12px; }}
.info {{ bottom:12px; left:12px; }}
.legend div {{ margin:3px 0; }}
.legend span {{
  display:inline-block; width:10px; height:10px;
  border-radius:50%; margin-right:6px; vertical-align:middle;
}}
</style>
</head>
<body>
<div class="tip" id="t"></div>
<div class="legend">
  <div><span style="background:#f97316"></span>source</div>
  <div><span style="background:#a78bfa"></span>entity</div>
  <div><span style="background:#34d399"></span>concept</div>
</div>
<div class="info">{info}</div>
<svg id="g"></svg>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<script>
const N={nodes_json}, L={links_json};
const C={{source:"#f97316",entity:"#a78bfa",concept:"#34d399"}};
const R={{source:5,entity:7,concept:7}};
const svg=d3.select("#g"),w=innerWidth,h=innerHeight;
svg.attr("viewBox",[0,0,w,h]);
const sim=d3.forceSimulation(N)
  .force("link",d3.forceLink(L).id(d=>d.id).distance(80))
  .force("charge",d3.forceManyBody().strength(-180))
  .force("center",d3.forceCenter(w/2,h/2))
  .force("collide",d3.forceCollide().radius(d=>(R[d.type]||6)+8));
const g=svg.append("g");
svg.call(d3.zoom().scaleExtent([.15,5]).on("zoom",e=>g.attr("transform",e.transform)));
const link=g.append("g").selectAll("line").data(L).enter().append("line")
  .attr("class","link").attr("stroke","#30363d");
const node=g.append("g").selectAll("g").data(N).enter().append("g").attr("class","node")
  .call(d3.drag()
    .on("start",(e,d)=>{{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y}})
    .on("drag",(e,d)=>{{d.fx=e.x;d.fy=e.y}})
    .on("end",(e,d)=>{{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}}));
node.append("circle").attr("r",d=>R[d.type]||6)
  .attr("fill",d=>C[d.type]||"#888")
  .attr("stroke",d=>d3.color(C[d.type]||"#888").brighter(.5));
node.append("text").attr("dx",d=>(R[d.type]||6)+4).attr("dy",4)
  .text(d=>d.label.length>28?d.label.slice(0,25)+"...":d.label);
const tip=document.getElementById("t");
node.on("mouseover",(e,d)=>{{
  tip.style.display="block";
  tip.innerHTML=`<b>${{d.label}}</b><br>${{d.type}} · ${{d.src||0}} source(s)`;
}}).on("mousemove",e=>{{
  tip.style.left=(e.pageX+12)+"px";tip.style.top=(e.pageY-20)+"px";
}}).on("mouseout",()=>tip.style.display="none");
sim.on("tick",()=>{{
  link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
      .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  node.attr("transform",d=>`translate(${{d.x}},${{d.y}})`);
}});
</script>
</body>
</html>
"""


@cli.command()
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
def graph(output: Optional[Path]) -> None:
    """Génère un graphe interactif des relations du wiki (HTML + d3.js)."""
    pages = list_wiki_pages()
    if not pages:
        click.echo("[graph] wiki vide.")
        return

    nodes_list: list[dict[str, Any]] = []
    links_list: list[dict[str, str]] = []
    slug_set: set[str] = set()

    for p in pages:
        slug = p.stem
        slug_set.add(slug)
        try:
            post = frontmatter.load(p)
            meta = post.metadata
        except Exception:
            meta = {}

        ptype = meta.get("type", "concept")
        label = meta.get("name") or meta.get("title") or slug
        src_count = len(meta.get("sources", []))
        nodes_list.append({"id": slug, "label": label, "type": ptype, "src": src_count})

        full_text = frontmatter.dumps(post) if meta else p.read_text("utf-8", errors="replace")
        for match in LINK_RE.finditer(full_text):
            target = match.group(1).strip()
            if target != slug:
                links_list.append({"source": slug, "target": target})

    # Nœuds fantômes pour cibles manquantes
    all_targets = {lnk["target"] for lnk in links_list}
    for t in all_targets - slug_set:
        nodes_list.append({"id": t, "label": t.replace("-", " ").title(), "type": "concept", "src": 0})
        slug_set.add(t)

    # Dédoublonner les liens
    seen_links: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for lnk in links_list:
        key = (lnk["source"], lnk["target"])
        if key not in seen_links and lnk["target"] in slug_set:
            seen_links.add(key)
            deduped.append(lnk)

    info = (
        f"{len(nodes_list)} nœuds · {len(deduped)} liens · "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    html = GRAPH_HTML.format(
        nodes_json=json.dumps(nodes_list, ensure_ascii=False),
        links_json=json.dumps(deduped, ensure_ascii=False),
        info=info,
    )

    out_path = output or (PROJECT_ROOT / "wiki-graph.html")
    out_path.write_text(html, encoding="utf-8")
    click.echo(f"[graph] {out_path} — {len(nodes_list)} nœuds, {len(deduped)} liens")
    click.echo("  Ouvrir dans un navigateur pour explorer.")


# ---------------------------------------------------------------------------
# Commande : suggest — lint sémantique + pistes d'exploration
# ---------------------------------------------------------------------------

SUGGEST_SYSTEM = """\
Tu es un assistant d'analyse de wiki. On te donne l'index d'un wiki
personnel. Identifie :

1. **Questions à explorer** : 3-5 questions intéressantes à poser au wiki
   ou à creuser avec de nouvelles sources.
2. **Sources manquantes** : 2-3 types de sources qui enrichiraient le wiki,
   avec des suggestions concrètes.
3. **Pages à créer** : 2-4 concepts ou entités mentionnés mais sans page dédiée.
4. **Connexions sous-exploitées** : liens entre pages qui mériteraient
   d'être renforcés.

Réponds en markdown concis avec des liens [[slug]].
"""

SUGGEST_DEEP_SYSTEM = """\
Tu es un assistant d'analyse de wiki. On te donne le contenu de plusieurs
pages. Identifie :

1. **Contradictions** entre pages.
2. **Lacunes** : sujets traités superficiellement.
3. **Patterns** : thèmes récurrents ou connexions implicites.
4. **Prochaines étapes** : 3-5 actions concrètes.

Markdown concis avec liens [[slug]].
"""


@cli.command()
@click.option("--deep", is_flag=True, help="Analyse profonde (lit les pages, pas seulement l'index).")
def suggest(deep: bool) -> None:
    """Le LLM analyse le wiki et propose des pistes d'exploration."""
    index = read_index()
    if not index.strip() or "## Sources" not in index:
        click.echo("[suggest] wiki trop vide pour des suggestions.")
        return

    if not deep:
        click.echo("[suggest] analyse de l'index...")
        result = llm_chat(
            system=SUGGEST_SYSTEM,
            user=f"Index du wiki :\n\n{index}",
            max_tokens=1500,
        )
    else:
        click.echo("[suggest] analyse profonde des pages...")
        pages = list_wiki_pages()
        budget = token_budget() - estimate_tokens(SUGGEST_DEEP_SYSTEM) - 500
        loaded: list[str] = []
        tokens_used = 0

        # Prioriser concepts > entités > sources
        priority = sorted(
            pages,
            key=lambda p: (0 if "concepts" in p.parts else 1 if "entities" in p.parts else 2),
        )
        for p in priority:
            content = p.read_text(encoding="utf-8")
            ct = estimate_tokens(content)
            if tokens_used + ct > budget:
                break
            loaded.append(f"### [[{p.stem}]]\n\n{content}")
            tokens_used += ct

        click.echo(f"  {len(loaded)} page(s) chargées ({tokens_used} tokens)")
        pages_blob = "\n\n---\n\n".join(loaded)
        result = llm_chat(
            system=SUGGEST_DEEP_SYSTEM,
            user=f"Pages du wiki :\n\n{pages_blob}",
            max_tokens=1500,
        )

    click.echo("\n" + "=" * 60)
    click.echo(result)
    click.echo("=" * 60)

    append_log("note", f"suggest {'deep' if deep else 'light'}", "- Suggestions affichées.\n")


# ---------------------------------------------------------------------------
# Commande : shell — REPL interactif (toutes les commandes)
# ---------------------------------------------------------------------------


@cli.command()
def shell() -> None:
    """Mode interactif : charge le modèle une fois, accepte des commandes en boucle."""
    click.echo("=" * 60)
    click.echo("  LLM Wiki — Mode interactif")
    click.echo("  Tape 'help' pour la liste des commandes.")
    click.echo("=" * 60)
    click.echo()

    click.echo("Chargement du modèle (une seule fois)...")
    _get_llm()
    click.echo()

    while True:
        try:
            line = input("wiki> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nAu revoir.")
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if cmd in ("quit", "exit", "q"):
                click.echo("Au revoir.")
                break

            elif cmd == "ingest":
                if not arg:
                    click.echo("  Usage : ingest <chemin-fichier>")
                    continue
                path = Path(arg)
                if not path.is_absolute():
                    path = PROJECT_ROOT / path
                if not path.exists():
                    click.echo(f"  [erreur] fichier introuvable : {path}")
                    continue
                cli.main(["ingest", str(path), "--batch"], standalone_mode=False)

            elif cmd == "query":
                if not arg:
                    click.echo("  Usage : query <question> [--file-back]")
                    continue
                fb = "--file-back" in arg
                q = arg.replace("--file-back", "").strip()
                a = ["query", q]
                if fb:
                    a.append("--file-back")
                cli.main(a, standalone_mode=False)

            elif cmd == "search":
                if not arg:
                    click.echo("  Usage : search <termes>")
                    continue
                cli.main(["search"] + arg.split(), standalone_mode=False)

            elif cmd == "lint":
                la = ["lint"]
                if "--auto-fix" in arg:
                    la.append("--auto-fix")
                elif "--fix-index" in arg:
                    la.append("--fix-index")
                cli.main(la, standalone_mode=False)

            elif cmd == "overview":
                cli.main(["overview"], standalone_mode=False)

            elif cmd == "stats":
                cli.main(["stats"], standalone_mode=False)

            elif cmd == "export":
                if not arg:
                    click.echo("  Usage : export <slug> [--format html|marp]")
                    continue
                cli.main(["export"] + arg.split(), standalone_mode=False)

            elif cmd in ("batch-ingest", "batch"):
                ba = ["batch-ingest"]
                if arg:
                    ba += arg.split()
                cli.main(ba, standalone_mode=False)

            elif cmd == "graph":
                cli.main(["graph"], standalone_mode=False)

            elif cmd == "suggest":
                sa = ["suggest"]
                if "--deep" in arg:
                    sa.append("--deep")
                cli.main(sa, standalone_mode=False)

            elif cmd == "help":
                click.echo("  Commandes :")
                click.echo("    ingest <fichier>              Ingérer une source")
                click.echo("    batch-ingest [dossier]        Ingérer un dossier entier")
                click.echo("    query <question> [--file-back]")
                click.echo("    search <termes> [-n N]        Recherche full-text BM25")
                click.echo("    lint [--fix-index|--auto-fix] Vérifier / corriger")
                click.echo("    overview                      Synthèse globale")
                click.echo("    stats                         Dashboard")
                click.echo("    export <slug> [--format html|marp]")
                click.echo("    graph                         Graphe HTML interactif")
                click.echo("    suggest [--deep]              Suggestions LLM")
                click.echo("    quit                          Quitter")

            else:
                click.echo(f"  Commande inconnue : {cmd}. Tape 'help'.")

        except SystemExit:
            pass
        except Exception as e:
            click.echo(f"  [erreur] {type(e).__name__}: {e}")

        click.echo()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
