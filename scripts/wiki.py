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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import click
import frontmatter
import yaml

# Force UTF-8 on stdout/stderr — Windows defaults to cp1252 or ascii
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# llama_cpp est importé paresseusement dans _get_llm() pour que les commandes
# qui n'appellent pas le LLM (lint) démarrent sans charger CUDA.

# ---------------------------------------------------------------------------
# Chemins et configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "SCHEMA.md"
WORKSPACES_PATH = PROJECT_ROOT / "workspaces.json"

SECRETS_PATH = PROJECT_ROOT / "secrets.yaml"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        click.echo(f"[erreur] config.yaml introuvable à {CONFIG_PATH}")
        sys.exit(1)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Fusionne les clés API depuis secrets.yaml (gitignored)
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("r", encoding="utf-8") as f:
            secrets = yaml.safe_load(f) or {}
        if "api_keys" in secrets:
            cfg.setdefault("llm_provider", {}).setdefault("api_keys", {}).update(secrets["api_keys"])

    return cfg


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


def _resolve_default_ctx() -> "WikiContext":
    """Lit workspaces.json pour déterminer le corpus actif.
    Fallback sur les chemins config.yaml si workspaces.json est absent ou illisible."""
    if WORKSPACES_PATH.exists():
        try:
            ws = json.loads(WORKSPACES_PATH.read_text(encoding="utf-8"))
            active = ws.get("active", "default")
            entry = ws.get("corpora", {}).get(active, {})
            if "path" in entry:
                base = PROJECT_ROOT / entry["path"]
                return WikiContext.from_paths(base / "wiki", base / "raw")
            if "wiki_dir" in entry and "raw_dir" in entry:
                return WikiContext.from_paths(
                    PROJECT_ROOT / entry["wiki_dir"],
                    PROJECT_ROOT / entry["raw_dir"],
                )
        except Exception:
            pass
    return WikiContext.from_paths(WIKI_DIR, RAW_DIR)


def _resolve_ctx_for_cmd(corpus: Optional[str]) -> "WikiContext":
    """Résout le WikiContext pour une commande CLI.
    Si corpus est fourni, cherche l'entrée dans workspaces.json.
    Sinon, utilise le corpus actif (DEFAULT_CTX)."""
    if corpus is None:
        return _resolve_default_ctx()
    if WORKSPACES_PATH.exists():
        try:
            ws = json.loads(WORKSPACES_PATH.read_text(encoding="utf-8"))
            entry = ws.get("corpora", {}).get(corpus)
            if entry is None:
                available = ", ".join(ws.get("corpora", {}).keys())
                raise click.BadParameter(
                    f"Corpus inconnu : {corpus!r}. Disponibles : {available}"
                )
            if "path" in entry:
                base = PROJECT_ROOT / entry["path"]
                return WikiContext.from_paths(base / "wiki", base / "raw")
            if "wiki_dir" in entry and "raw_dir" in entry:
                return WikiContext.from_paths(
                    PROJECT_ROOT / entry["wiki_dir"],
                    PROJECT_ROOT / entry["raw_dir"],
                )
        except click.BadParameter:
            raise
        except Exception as e:
            raise click.BadParameter(f"Erreur lecture workspaces.json : {e}")
    raise click.BadParameter(f"workspaces.json introuvable, impossible de résoudre {corpus!r}")


DEFAULT_CTX: WikiContext = _resolve_default_ctx()


# ---------------------------------------------------------------------------
# Budget tokens — estimation légère sans tokenizer externe
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Estimation grossière : ~4 caractères = 1 token en anglais/français.
    Suffisant pour le budget management, pas pour la facturation."""
    return len(text) // 4


def token_budget() -> int:
    """Tokens disponibles pour le contenu (prompt + réponse).

    En mode local, laisse une marge de 20% sous n_ctx.
    En mode API, retourne un budget beaucoup plus grand (32k OpenAI/Anthropic,
    16k Mistral) — le chunking dans summarize_long_source devient rare.
    """
    provider_cfg = CFG.get("llm_provider", {})
    if provider_cfg.get("mode") == "api":
        provider = provider_cfg.get("api_provider", "openai")
        return 16_000 if provider == "mistral" else 32_000
    n_ctx = CFG["model"]["n_ctx"]
    max_response = CFG["inference"]["max_tokens"]
    return int(n_ctx * 0.80) - max_response


# ---------------------------------------------------------------------------
# Abstraction LLM — backends Local et API
# ---------------------------------------------------------------------------

class LLMBackend(ABC):
    @abstractmethod
    def chat(self, system: str, user: str, *, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str: ...

    @abstractmethod
    def chat_stream(self, system: str, user: str) -> Iterator[str]: ...


class LocalBackend(LLMBackend):
    """Backend llama-cpp-python (modèle GGUF local)."""

    def chat(self, system, user, *, temperature=None, max_tokens=None) -> str:
        llm = _get_llm()
        inf = CFG["inference"]
        resp = llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature if temperature is not None else inf["temperature"],
            top_p=inf["top_p"],
            max_tokens=max_tokens if max_tokens is not None else inf["max_tokens"],
        )
        return resp["choices"][0]["message"]["content"].strip()

    def chat_stream(self, system, user) -> Iterator[str]:
        llm = _get_llm()
        inf = CFG["inference"]
        for chunk in llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=inf["temperature"],
            top_p=inf["top_p"],
            max_tokens=inf["max_tokens"],
            stream=True,
        ):
            delta = chunk["choices"][0]["delta"].get("content", "")
            if delta:
                yield delta


class APIBackend(LLMBackend):
    """Backend API externe : OpenAI, Anthropic ou Mistral."""

    def __init__(self) -> None:
        provider_cfg = CFG.get("llm_provider", {})
        self.provider: str = provider_cfg.get("api_provider", "openai")
        self.model: str = provider_cfg.get("api_model", "gpt-4o")
        api_keys: dict = provider_cfg.get("api_keys", {})
        self.api_key: str = api_keys.get(self.provider, "")

    def _temp(self, t: Optional[float]) -> float:
        return t if t is not None else CFG["inference"]["temperature"]

    def _max_tok(self, m: Optional[int]) -> int:
        return m if m is not None else CFG["inference"]["max_tokens"]

    # --- OpenAI ---

    def _openai_client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "Package openai non installé. Lancez : pip install openai>=1.0"
            )
        if not self.api_key:
            raise RuntimeError("Clé API OpenAI manquante. Configurez-la dans Paramètres.")
        return OpenAI(api_key=self.api_key)

    def _chat_openai(self, system, user, *, temperature=None, max_tokens=None) -> str:
        client = self._openai_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._temp(temperature),
            max_tokens=self._max_tok(max_tokens),
        )
        return resp.choices[0].message.content.strip()

    def _stream_openai(self, system, user) -> Iterator[str]:
        client = self._openai_client()
        for chunk in client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._temp(None),
            max_tokens=self._max_tok(None),
            stream=True,
        ):
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta

    # --- Anthropic ---

    def _anthropic_client(self):
        try:
            import anthropic as _ant
        except ImportError:
            raise RuntimeError(
                "Package anthropic non installé. Lancez : pip install anthropic>=0.20"
            )
        if not self.api_key:
            raise RuntimeError("Clé API Anthropic manquante. Configurez-la dans Paramètres.")
        return _ant.Anthropic(api_key=self.api_key)

    def _chat_anthropic(self, system, user, *, temperature=None, max_tokens=None) -> str:
        client = self._anthropic_client()
        resp = client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self._temp(temperature),
            max_tokens=self._max_tok(max_tokens),
        )
        return resp.content[0].text.strip()

    def _stream_anthropic(self, system, user) -> Iterator[str]:
        client = self._anthropic_client()
        with client.messages.stream(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=self._temp(None),
            max_tokens=self._max_tok(None),
        ) as stream:
            yield from stream.text_stream

    # --- Mistral ---

    def _mistral_client(self):
        try:
            from mistralai import Mistral
        except ImportError:
            raise RuntimeError(
                "Package mistralai non installé. Lancez : pip install mistralai>=0.4"
            )
        if not self.api_key:
            raise RuntimeError("Clé API Mistral manquante. Configurez-la dans Paramètres.")
        return Mistral(api_key=self.api_key)

    def _chat_mistral(self, system, user, *, temperature=None, max_tokens=None) -> str:
        client = self._mistral_client()
        resp = client.chat.complete(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._temp(temperature),
            max_tokens=self._max_tok(max_tokens),
        )
        return resp.choices[0].message.content.strip()

    def _stream_mistral(self, system, user) -> Iterator[str]:
        client = self._mistral_client()
        for chunk in client.chat.stream(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self._temp(None),
            max_tokens=self._max_tok(None),
        ):
            delta = chunk.data.choices[0].delta.content or ""
            if delta:
                yield delta

    # --- Dispatch ---

    def chat(self, system, user, *, temperature=None, max_tokens=None) -> str:
        if self.provider == "openai":
            return self._chat_openai(system, user, temperature=temperature, max_tokens=max_tokens)
        if self.provider == "anthropic":
            return self._chat_anthropic(system, user, temperature=temperature, max_tokens=max_tokens)
        if self.provider == "mistral":
            return self._chat_mistral(system, user, temperature=temperature, max_tokens=max_tokens)
        raise ValueError(f"Fournisseur inconnu : {self.provider}")

    def chat_stream(self, system, user) -> Iterator[str]:
        if self.provider == "openai":
            yield from self._stream_openai(system, user)
        elif self.provider == "anthropic":
            yield from self._stream_anthropic(system, user)
        elif self.provider == "mistral":
            yield from self._stream_mistral(system, user)
        else:
            raise ValueError(f"Fournisseur inconnu : {self.provider}")


def get_active_backend() -> LLMBackend:
    """Retourne le backend LLM actif selon config.yaml."""
    provider_cfg = CFG.get("llm_provider", {})
    if provider_cfg.get("mode") == "api":
        return APIBackend()
    return LocalBackend()


# ---------------------------------------------------------------------------
# Singleton LLM (backend local uniquement)
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
        click.echo("Télécharge le GGUF et mets à jour config.yaml.")
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
    """Appel chat simple. Délègue au backend actif."""
    return get_active_backend().chat(system, user, temperature=temperature, max_tokens=max_tokens)


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

def _fm_load(path: Path) -> "frontmatter.Post":
    """Charge un fichier frontmatter en forçant l'encodage UTF-8."""
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return frontmatter.load(fh)


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
    return SCHEMA_PATH.read_text(encoding="utf-8", errors="replace")


def read_index(ctx: WikiContext = None) -> str:
    index_file = (ctx or DEFAULT_CTX).index_file
    if not index_file.exists():
        return ""
    return index_file.read_text(encoding="utf-8", errors="replace")


def _extract_page_description(page_path: Path, max_len: int = 120) -> str:
    """Extrait une description courte depuis le frontmatter ou le contenu d'une page.
    Priorité : (1) première phrase du contenu, (2) champ name/title du frontmatter,
    (3) slug formaté en titre."""
    try:
        post = _fm_load(page_path)
        # 1. Première ligne non-vide du contenu (hors titre H1)
        content = post.content.strip()
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sentence = re.split(r"(?<=\.)\s", line)[0][:max_len].strip()
                if len(sentence) > 10:
                    return sentence
        # 2. Champ name ou title du frontmatter
        name = post.metadata.get("name") or post.metadata.get("title")
        if name:
            return str(name).splitlines()[0][:max_len]
    except Exception:
        pass
    # 3. Slug formaté
    return page_path.stem.replace("-", " ").title()


def append_log(op: str, title: str, body: str, ctx: WikiContext = None) -> None:
    log_file = (ctx or DEFAULT_CTX).log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Déduplication : évite les runs répétitifs de la même opération en < 60 s.
    if log_file.exists():
        log_content = log_file.read_text(encoding="utf-8", errors="replace")
        last_match = None
        for m in re.finditer(
            r"^## \[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] (\w+) \|",
            log_content,
            re.MULTILINE,
        ):
            last_match = m
        if last_match and last_match.group(2) == op:
            try:
                last_ts = datetime.strptime(last_match.group(1), "%Y-%m-%d %H:%M")
                if (datetime.now() - last_ts).total_seconds() < 60:
                    return
            except ValueError:
                pass

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"\n## [{timestamp}] {op} | {title}\n\n"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(header + body.rstrip() + "\n")


def _check_not_in_raw(path: Path) -> None:
    """Lève une erreur si le chemin cible se trouve dans un répertoire raw/.
    Appelé avant toute écriture pour garantir l'immuabilité des sources (SCHEMA §12)."""
    parts = path.resolve().parts
    if "raw" in parts:
        raise ValueError(
            f"Refus d'écrire dans raw/ : {path}\n"
            "Les fichiers sources sont immuables (SCHEMA §12)."
        )


@dataclass
class WikiPage:
    path: Path
    meta: dict[str, Any]
    content: str

    @classmethod
    def load(cls, path: Path) -> "WikiPage":
        post = _fm_load(path)
        return cls(path=path, meta=dict(post.metadata), content=post.content)

    def save(self) -> None:
        _check_not_in_raw(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(self.content, **self.meta)
        self.path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def list_wiki_pages(ctx: WikiContext = None) -> list[Path]:
    """Liste toutes les pages .md du wiki sauf index, log, overview.
    Inclut sources/, entities/, concepts/ et analyses/."""
    c = ctx or DEFAULT_CTX
    if not c.wiki_dir.exists():
        return []
    excluded = {c.index_file.resolve(), c.log_file.resolve(), c.overview_file.resolve()}
    return [p for p in c.wiki_dir.rglob("*.md") if p.resolve() not in excluded
            and ".obsidian" not in p.parts]


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
    """Si le texte dépasse le budget, résume puis retourne.
    Retourne le texte original s'il tient dans le budget.

    En mode API, le grand contexte disponible évite le chunking dans la plupart
    des cas. Si le texte est quand même trop long, une seule passe de résumé
    dense est effectuée (pas de chunking en N passes).
    """
    provider_cfg = CFG.get("llm_provider", {})
    content_budget = token_budget() - 2500  # marge pour prompt extraction + index

    if estimate_tokens(text) <= content_budget:
        return text

    click.echo(
        f"[ingest] source longue ({estimate_tokens(text)} tokens estimés), résumé…"
    )

    if provider_cfg.get("mode") == "api":
        # Passe unique : on tronque le texte brut au budget puis on résume
        char_budget = content_budget * 4
        return llm_chat(
            system=SUMMARIZE_CHUNK_SYSTEM,
            user=f"Résume ce texte en conservant tous les points importants :\n\n{text[:char_budget]}",
            max_tokens=3000,
        )

    # Backend local : résumé par passes de chunks
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
    "- Slugs en kebab-case ASCII, minuscules (sans accents, même pour les termes français)\n"
    "- Tags en minuscules kebab-case\n"
    "- Détecte les contradictions avec l'index existant\n"
    "- Les pages seront rédigées en français\n\n"
    "RÈGLE STRICTE — classification entité vs concept :\n"
    "  ENTITÉ = nom propre identifiable : entreprise, personne, produit commercial, "
    "lieu géographique.\n"
    "  → Exemples entités : Nvidia, Hugging Face, Google, Meta, Qualcomm, Gemma, "
    "MediaTek, Raspberry Pi, Vertex AI, Keras, Ollama, vLLM, LM Studio, Unsloth, "
    "Llama.cpp, MLX, Kaggle, Cloud Run, GKE, AI Core\n"
    "  CONCEPT = idée, méthode, technique, phénomène, mécanisme, domaine.\n"
    "  → Exemples concepts : quantification de vecteurs, inférence, open source, "
    "modèle de langage, compression de données, décentralisation, fine-tuning, "
    "retrieval augmented generation, intelligence artificielle\n"
    "  En cas de doute : si c'est un nom propre (majuscule en anglais, identifiable "
    "sans contexte) → entité. Si c'est une idée ou technique générique → concept.\n\n"
    "RÈGLE — classification source_kind :\n"
    "  'article'       : article web, billet de blog, actualité, presse\n"
    "  'paper'         : article académique (présence d'abstract, DOI, références biblio)\n"
    "  'podcast-notes' : transcription audio, notes de podcast, langage oral, Q&R\n"
    "  'book-chapter'  : extrait de livre, chapitre numéroté, style éditorial\n"
    "  'transcript'    : transcription de conférence, vidéo, discours\n"
    "  'other'         : tout ce qui ne rentre pas dans les catégories ci-dessus\n"
    "  Par défaut si incertain : 'article'."
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
  "source_kind": "article",
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
"source_kind" = classification selon la règle ci-dessus.
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
@click.option(
    "--corpus",
    default=None,
    metavar="ID",
    help="Corpus cible (défaut : corpus actif dans workspaces.json).",
)
@click.pass_context
def cli(ctx, corpus) -> None:
    """LLM Wiki — CLI pour ingest, query, lint, overview."""
    ctx.ensure_object(dict)
    wc = _resolve_ctx_for_cmd(corpus)
    ctx.obj["wiki_ctx"] = wc
    ctx.obj["corpus_id"] = corpus
    try:
        rel = str(wc.wiki_dir.parent.relative_to(PROJECT_ROOT))
    except ValueError:
        rel = str(wc.wiki_dir.parent)
    click.echo(f"[corpus: {rel}]")


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
@click.pass_context
def ingest(ctx, source_path: Path, batch: bool) -> None:
    """Ingère une source depuis raw/ et met à jour le wiki."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    source_path = source_path.resolve()

    # Chemin relatif pour le frontmatter : relatif à raw/ si possible, sinon au projet
    try:
        source_rel = source_path.relative_to(wiki_ctx.raw_dir.resolve())
    except ValueError:
        try:
            source_rel = source_path.relative_to(PROJECT_ROOT)
        except ValueError:
            source_rel = source_path

    click.echo(f"[ingest] lecture de {source_rel}")
    raw_text = source_path.read_text(encoding="utf-8", errors="replace")

    source_content = summarize_long_source(raw_text)
    index = read_index(wiki_ctx) or "(index vide)"

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
    source_page_path = wiki_ctx.wiki_dir / "sources" / f"{source_page_slug}.md"

    counter = 2
    while source_page_path.exists():
        source_page_slug = f"{today}-{slug}-{counter}"
        source_page_path = wiki_ctx.wiki_dir / "sources" / f"{source_page_slug}.md"
        counter += 1

    entities_links = [
        f"[[{slugify(e.get('slug') or e['name'])}]]"
        for e in data.get("entities", [])
    ]
    concepts_links = [
        f"[[{slugify(c.get('slug') or c['name'])}]]"
        for c in data.get("concepts", [])
    ]

    valid_kinds = {"article", "paper", "podcast-notes", "book-chapter", "transcript", "other"}
    source_kind = data.get("source_kind", "article")
    if source_kind not in valid_kinds:
        source_kind = "article"

    source_meta = {
        "type": "source",
        "title": data.get("title", "Sans titre"),
        "slug": slug,
        "ingested": today,
        "source_path": str(source_rel).replace("\\", "/"),
        "source_kind": source_kind,
        "tags": data.get("tags", []),
        "related_entities": entities_links,
        "related_concepts": concepts_links,
    }

    key_points_md = "\n".join(f"- {kp}" for kp in data.get("key_points", []))
    contradictions = data.get("contradictions") or []
    contradictions_md = (
        "\n\n## Contradictions détectées\n\n"
        + "\n".join(f"- {c}" for c in contradictions)
    ) if contradictions else ""

    # Sections entités et concepts omises si vides (pas de placeholder _aucune_)
    entities_section = (
        f"\n\n## Entités mentionnées\n\n{', '.join(entities_links)}"
        if entities_links else ""
    )
    concepts_section = (
        f"\n\n## Concepts mentionnés\n\n{', '.join(concepts_links)}"
        if concepts_links else ""
    )

    source_content_md = (
        f"# {data.get('title', 'Sans titre')}\n\n"
        f"{data.get('summary_one_line', '')}\n\n"
        f"## Points clés\n\n"
        f"{key_points_md}"
        f"{contradictions_md}"
        f"{entities_section}"
        f"{concepts_section}\n"
    )

    WikiPage(source_page_path, source_meta, source_content_md).save()
    click.echo(
        f"[ingest] page source créée : "
        f"{source_page_path.relative_to(PROJECT_ROOT)}"
    )

    created_stubs: list[str] = []
    updated_stubs: list[str] = []

    for ent in data.get("entities", []):
        ent_slug = slugify(ent.get("slug") or ent["name"])
        ent_path = wiki_ctx.wiki_dir / "entities" / f"{ent_slug}.md"

        if ent.get("new") or not ent_path.exists():
            WikiPage(ent_path, {
                "type": "entity", "name": ent["name"], "slug": ent_slug,
                "kind": ent.get("kind", "other"), "aliases": [],
                "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today, "tags": data.get("tags", []),
            }, f"# {ent['name']}\n\n{ent.get('note', '')}\n").save()
            created_stubs.append(f"[[{ent_slug}]] (entité)")
        else:
            _enrich_existing_page(ent_path, source_page_slug, ent.get("note", ""), today, updated_stubs, "entité")

    for cpt in data.get("concepts", []):
        cpt_slug = slugify(cpt.get("slug") or cpt["name"])
        cpt_path = wiki_ctx.wiki_dir / "concepts" / f"{cpt_slug}.md"

        if cpt.get("new") or not cpt_path.exists():
            WikiPage(cpt_path, {
                "type": "concept", "name": cpt["name"], "slug": cpt_slug,
                "aliases": [], "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today, "tags": data.get("tags", []),
            }, f"# {cpt['name']}\n\n{cpt.get('note', '')}\n").save()
            created_stubs.append(f"[[{cpt_slug}]] (concept)")
        else:
            _enrich_existing_page(cpt_path, source_page_slug, cpt.get("note", ""), today, updated_stubs, "concept")

    # --- Index ---
    src_tags_str = " · ".join(data.get("tags", [])[:3])
    src_meta_str = "source" + (f" · {src_tags_str}" if src_tags_str else "") + f" · ingéré {today}"
    _update_index_entry(
        "Sources",
        f"- [[{source_page_slug}]] — {data.get('summary_one_line', '')} _({src_meta_str})_",
        wiki_ctx,
    )
    ent_tags_str = " · ".join(data.get("tags", [])[:2])
    for ent in data.get("entities", []):
        if ent.get("new"):
            es = slugify(ent.get("slug") or ent["name"])
            note_line = (ent.get("note", "") or ent["name"]).splitlines()[0]
            kind = ent.get("kind", "other")
            em = f"entité · {kind}" + (f" · {ent_tags_str}" if ent_tags_str else "")
            _update_index_entry("Entités", f"- [[{es}]] — {note_line} _({em})_", wiki_ctx)
    cpt_tags_str = " · ".join(data.get("tags", [])[:2])
    for cpt in data.get("concepts", []):
        if cpt.get("new"):
            cs = slugify(cpt.get("slug") or cpt["name"])
            note_line = (cpt.get("note", "") or cpt["name"]).splitlines()[0]
            cm = "concept" + (f" · {cpt_tags_str}" if cpt_tags_str else "")
            _update_index_entry("Concepts", f"- [[{cs}]] — {note_line} _({cm})_", wiki_ctx)

    # --- Log ---
    _link_re = re.compile(r"(\[\[[^\]]+\]\])")
    new_ents = [s for s in created_stubs if "(entité)" in s]
    new_cpts = [s for s in created_stubs if "(concept)" in s]

    def _stub_link(s: str) -> str:
        m = _link_re.match(s)
        return m.group(1) if m else s

    log_parts = [
        f"- **Source** : `{source_rel}`",
        f"- **Créé** : [[{source_page_slug}]]",
    ]
    if new_ents:
        log_parts.append("- **Entités** : " + " · ".join(_stub_link(s) for s in new_ents))
    if new_cpts:
        log_parts.append("- **Concepts** : " + " · ".join(_stub_link(s) for s in new_cpts))
    if updated_stubs:
        log_parts.append("- **Enrichi** : " + " · ".join(updated_stubs))
    if contradictions:
        log_parts.append(f"- **Contradictions** : {len(contradictions)}")

    append_log("ingest", data.get("title", slug), "\n".join(log_parts) + "\n", wiki_ctx)

    click.echo("[ingest] terminé.")
    click.echo(f"  pages créées    : {1 + len(created_stubs)}")
    click.echo(f"  pages enrichies : {len(updated_stubs)}")
    if contradictions:
        click.echo(f"  /!\\ {len(contradictions)} contradiction(s) :")
        for c in contradictions:
            click.echo(f"      • {c}")

    if not batch:
        _update_index_overview(wiki_ctx)
        click.echo("[ingest] mise à jour de overview.md...")
        _regenerate_overview(wiki_ctx)


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

    text = index_file.read_text(encoding="utf-8", errors="replace")
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


def _update_index_overview(ctx: WikiContext = None) -> None:
    """Régénère la section ## Vue d'ensemble en tête de index.md (après le H1).
    Appel LLM léger basé sur les sources listées dans l'index."""
    index_file = (ctx or DEFAULT_CTX).index_file
    if not index_file.exists():
        return

    index_text = index_file.read_text(encoding="utf-8", errors="replace")

    # Extraire la section sources pour un prompt compact
    m = re.search(r"## Sources\n(.*?)(?=\n## |\Z)", index_text, re.DOTALL)
    prompt_content = m.group(0)[:1500] if m else index_text[:1500]

    summary = llm_chat(
        system=(
            "Tu es un mainteneur de wiki. En 2-3 phrases courtes, décris le périmètre "
            "thématique de ce wiki à partir des sources listées. Sois factuel et concis. "
            "Réponds en français, sans listes, sans tirets."
        ),
        user=prompt_content,
        max_tokens=150,
        temperature=0.2,
    ).strip()

    overview_block = f"## Vue d'ensemble\n\n{summary}\n\n"

    if "## Vue d'ensemble" in index_text:
        index_text = re.sub(
            r"## Vue d'ensemble\n\n.*?\n\n(?=##)",
            overview_block,
            index_text,
            flags=re.DOTALL,
            count=1,
        )
    else:
        # Insérer juste après le titre H1
        index_text = re.sub(
            r"(# [^\n]+\n\n)",
            r"\1" + overview_block,
            index_text,
            count=1,
        )

    index_file.write_text(index_text, encoding="utf-8")
    click.echo("[ingest] section Vue d'ensemble de l'index mise à jour.")


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


QUERY_SUGGEST_SYSTEM = (
    "Tu es un expert en gestion de connaissances. "
    "Une question a été posée mais le wiki ne contient aucune information pertinente. "
    "Propose 2-3 types de sources ou documents concrets à ingérer pour pouvoir répondre. "
    "Sois bref et pratique. Réponds en français avec des tirets."
)


@cli.command()
@click.argument("question")
@click.option(
    "--file-back",
    is_flag=True,
    help="Sauvegarde la réponse comme page wiki.",
)
@click.pass_context
def query(ctx, question: str, file_back: bool) -> None:
    """Pose une question au wiki."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    index = read_index(wiki_ctx)
    if not index.strip():
        click.echo("[query] index vide — commence par ingérer des sources.")
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
        click.echo("\n[query] génération de suggestions de sources à ingérer…")
        suggestions = llm_chat(
            system=QUERY_SUGGEST_SYSTEM,
            user=f"Question : {question}\n\nPropose 2-3 types de sources à ingérer.",
            max_tokens=300,
        )
        click.echo("\nSources suggérées :\n")
        click.echo(suggestions)
        append_log(
            "query", question[:80],
            "- Pages : (aucune)\n- Suggestions de sources : oui\n",
            wiki_ctx,
        )
        return

    all_pages = list_wiki_pages(wiki_ctx)
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
        content = p.read_text(encoding="utf-8", errors="replace")
        content_tokens = estimate_tokens(content)
        if tokens_used + content_tokens > budget:
            click.echo(f"  [budget] [[{clean}]] ignorée ({content_tokens} tok, budget {tokens_used}/{budget})")
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

    click.echo(f"[query] synthèse à partir de {len(loaded)} page(s) ({tokens_used} tokens)...")
    answer = llm_chat(
        system=QUERY_ANSWER_SYSTEM,
        user=QUERY_ANSWER_USER.format(question=question, pages=pages_blob),
    )

    click.echo("\n" + "=" * 60)
    click.echo(answer)
    click.echo("=" * 60)

    if file_back:
        _file_back_answer(question, answer, wiki_ctx)
    else:
        click.echo("\n  Tip : relancer avec --file-back pour sauvegarder cette réponse dans le wiki.")

    query_log_parts = ["- **Pages** : " + " · ".join(f"[[{s}]]" for s, _ in loaded)]
    if file_back:
        query_log_parts.append("- **File-back** : oui")
    append_log("query", question[:80], "\n".join(query_log_parts) + "\n", wiki_ctx)


_ANALYSIS_KEYWORDS = frozenset({
    "compare", "comparaison", "tableau", "table", "versus", "vs",
    "analyse", "synthèse", "bilan", "évaluation", "comparatif",
})


def _file_back_answer(question: str, answer: str, ctx: WikiContext = None) -> None:
    """Sauvegarde une réponse de query comme page wiki.
    Route vers concepts/ ou analyses/ selon la nature de la question."""
    c = ctx or DEFAULT_CTX
    today = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(question[:60])

    # Heuristique : mots-clés analytiques → analyses/, sinon → concepts/
    is_analysis = any(kw in question.lower() for kw in _ANALYSIS_KEYWORDS)
    subdir = "analyses" if is_analysis else "concepts"
    section = "Analyses" if is_analysis else "Concepts"

    slug_final = slug
    page_path = c.wiki_dir / subdir / f"{slug_final}.md"
    counter = 2
    while page_path.exists():
        slug_final = f"{slug}-{counter}"
        page_path = c.wiki_dir / subdir / f"{slug_final}.md"
        counter += 1

    refs = LINK_RE.findall(answer)
    tags = ["file-back", "analyse"] if is_analysis else ["file-back"]

    WikiPage(page_path, {
        "type": "concept",
        "name": question,
        "slug": slug_final,
        "aliases": [],
        "sources": [f"[[{r}]]" for r in refs],
        "origin_query": question,
        "origin_date": today,
        "last_updated": today,
        "tags": tags,
    }, f"# {question}\n\n{answer}\n").save()

    meta_str = f"concept · file-back · créé {today}"
    _update_index_entry(section, f"- [[{slug_final}]] — {question[:80]} _({meta_str})_", c)
    click.echo(f"\n[file-back] page créée : {page_path.relative_to(PROJECT_ROOT)}")


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
@click.pass_context
def overview(ctx) -> None:
    """Régénère la page de synthèse overview.md."""
    _regenerate_overview(ctx.obj["wiki_ctx"])


def _regenerate_overview(ctx: WikiContext = None) -> None:
    c = ctx or DEFAULT_CTX
    index = read_index(c)
    if not index.strip() or "## Sources" not in index:
        click.echo("[overview] pas assez de contenu pour une overview.")
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
    WikiPage(c.overview_file, meta, content).save()
    click.echo(f"[overview] page mise à jour : {c.overview_file.relative_to(PROJECT_ROOT)}")
    append_log("note", "overview régénéré", f"- Mis à jour le {today}.\n", c)


# ---------------------------------------------------------------------------
# Commande : lint
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--fix-index", is_flag=True, help="Corrige l'index désynchronisé automatiquement.")
@click.option("--auto-fix", is_flag=True, help="Corrige index + frontmatter invalide + liens cassés.")
@click.option("--semantic", is_flag=True, help="Active le lint sémantique (utilise le LLM).")
@click.pass_context
def lint(ctx, fix_index: bool, auto_fix: bool, semantic: bool) -> None:
    """Vérifie la cohérence du wiki (liens, frontmatter, index).
    Avec --semantic : ajoute la détection de contradictions, concepts manquants, claims obsolètes."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    if auto_fix:
        fix_index = True
    pages = list_wiki_pages(wiki_ctx)
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
            post = _fm_load(page_path)
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

    index_text = read_index(wiki_ctx)
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
                    content = page_path.read_text(encoding="utf-8", errors="replace")
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
                    post = _fm_load(page_path)
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
                desc = _extract_page_description(p)
                try:
                    post = _fm_load(p)
                    meta = post.metadata
                    tags_raw = meta.get("tags", [])[:2]
                    tags_str = " · ".join(str(t) for t in tags_raw)
                except Exception:
                    meta = {}
                    tags_str = ""

                if "entities" in p.parts:
                    kind = meta.get("kind", "other")
                    meta_str = f"entité · {kind}" + (f" · {tags_str}" if tags_str else "")
                    _update_index_entry("Entités", f"- [[{s}]] — {desc} _({meta_str})_", wiki_ctx)
                elif "analyses" in p.parts:
                    meta_str = "analyse" + (f" · {tags_str}" if tags_str else "")
                    _update_index_entry("Analyses", f"- [[{s}]] — {desc} _({meta_str})_", wiki_ctx)
                elif "concepts" in p.parts:
                    meta_str = "concept" + (f" · {tags_str}" if tags_str else "")
                    _update_index_entry("Concepts", f"- [[{s}]] — {desc} _({meta_str})_", wiki_ctx)
                elif "sources" in p.parts:
                    ingested = meta.get("ingested", "")
                    meta_str = "source" + (f" · {tags_str}" if tags_str else "") + (f" · ingéré {ingested}" if ingested else "")
                    _update_index_entry("Sources", f"- [[{s}]] — {desc} _({meta_str})_", wiki_ctx)
            click.echo("  [fix] index corrigé.")
        click.echo()

    if in_index_not_wiki:
        click.echo(
            "## Fantômes dans l'index (référencés mais inexistants)\n"
        )
        for s in sorted(in_index_not_wiki):
            click.echo(f"  [[{s}]]")
        click.echo()

    # ── Vérification des dates (structurelle) ────────────────────────────
    date_issues = _lint_date_consistency(pages, wiki_ctx)
    if date_issues:
        click.echo("## Dates incohérentes (last_updated < dernier ingest)\n")
        for issue in date_issues:
            click.echo(f"  [[{issue['slug']}]] — last_updated {issue['last_updated']} < ingest {issue['last_touched']}")
        click.echo()

    total_issues = (
        len(broken_links)
        + len(invalid_frontmatter)
        + len(orphans)
        + len(in_wiki_not_index)
        + len(in_index_not_wiki)
        + len(date_issues)
    )
    if total_issues == 0:
        click.echo("  Aucun problème structurel détecté.")

    append_log(
        "lint",
        "health check",
        f"- Pages: {len(pages)} | tokens: {total_tokens:,} | "
        f"liens cassés: {len(broken_links)} | "
        f"frontmatter: {len(invalid_frontmatter)} | "
        f"orphelines: {len(orphans)} | "
        f"désync index: {len(in_wiki_not_index) + len(in_index_not_wiki)} | "
        f"dates: {len(date_issues)}\n",
        wiki_ctx,
    )

    # ── Lint sémantique (optionnel, utilise le LLM) ───────────────────────
    if not semantic:
        return

    cache = _lint_cache_load(wiki_ctx)
    estimate = _semantic_lint_estimate(pages, wiki_ctx, cache)
    click.echo(f"\n[lint sémantique] ~{estimate} appel(s) LLM estimés (cache 24h actif).")
    if estimate > 0:
        click.confirm("Continuer ?", abort=True)

    click.echo("\n[lint sémantique] analyse des concepts manquants…")
    missing = _lint_missing_concepts(pages, wiki_ctx, cache)
    if missing:
        click.echo("\n## Concepts mentionnés sans page propre (top 10)\n")
        for item in missing:
            pages_str = ", ".join(f"[[{p}]]" for p in item["pages"])
            click.echo(f"  {item['name']:30s} cité {item['count']}x dans : {pages_str}")
    else:
        click.echo("  Aucun concept manquant détecté.")

    click.echo("\n[lint sémantique] détection des contradictions…")
    contradictions = _lint_contradictions(pages, wiki_ctx, cache)
    if contradictions:
        click.echo("\n## Contradictions inter-pages\n")
        for c in contradictions:
            click.echo(f"  [[{c['page_a']}]] ↔ [[{c['page_b']}]]")
            click.echo(f"    {c['explanation']}")
    else:
        click.echo("  Aucune contradiction détectée.")

    click.echo("\n[lint sémantique] analyse des lacunes thématiques…")
    gaps = _lint_source_gaps(wiki_ctx, cache)
    if gaps:
        click.echo("\n## Lacunes thématiques suggérées\n")
        for g in gaps:
            click.echo(f"  • {g['description']}")
            click.echo(f"    type: {g['source_type']} | mots-clés: {g['keywords']}")
    else:
        click.echo("  Aucune lacune détectée.")

    click.echo("\n[lint sémantique] détection des claims obsolètes…")
    stale = _lint_stale_claims(pages, wiki_ctx, cache)
    if stale:
        click.echo("\n## Pages avec affirmations possiblement obsolètes\n")
        for s in stale:
            click.echo(f"  [[{s['slug']}]] : {s['explanation']}")
            click.echo(f"    → {s['suggestion']}")
    else:
        click.echo("  Aucun claim obsolète détecté.")

    _lint_cache_save(cache, wiki_ctx)
    append_log("lint", "lint sémantique",
        f"- Concepts manquants: {len(missing)} | Contradictions: {len(contradictions)} | "
        f"Lacunes: {len(gaps)} | Obsolètes: {len(stale)}\n",
        wiki_ctx,
    )


# ---------------------------------------------------------------------------
# Lint sémantique — helpers partagés CLI et API
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import time as _time

_LINT_CACHE_FILE = ".lint_semantic_cache.json"
_LINT_CACHE_TTL = 86400  # 24h


def _lint_cache_load(ctx: WikiContext) -> dict:
    path = ctx.wiki_dir / _LINT_CACHE_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _lint_cache_save(cache: dict, ctx: WikiContext) -> None:
    path = ctx.wiki_dir / _LINT_CACHE_FILE
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cache_get(cache: dict, key: str) -> Any:
    entry = cache.get(key)
    if not entry:
        return None
    if _time.time() - entry.get("ts", 0) > _LINT_CACHE_TTL:
        return None
    return entry.get("data")


def _cache_set(cache: dict, key: str, data: Any) -> None:
    cache[key] = {"ts": _time.time(), "data": data}


def _content_hash(*texts: str) -> str:
    return _hashlib.md5("".join(texts).encode("utf-8")).hexdigest()[:12]


# ── Vérification structurelle : cohérence des dates ──────────────────────

def _lint_date_consistency(pages: list[Path], ctx: WikiContext) -> list[dict]:
    """Vérifie que last_updated de chaque page n'est pas antérieur au dernier
    ingest qui l'a touchée selon le log (vérification structurelle, sans LLM)."""
    log_file = ctx.log_file
    if not log_file.exists():
        return []

    log_text = log_file.read_text(encoding="utf-8", errors="replace")
    slug_last_touched: dict[str, str] = {}

    for entry in re.split(r"(?=^## \[)", log_text, flags=re.MULTILINE):
        m = re.match(r"## \[(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}\] (\w+) \|", entry)
        if not m or m.group(2) != "ingest":
            continue
        date_str = m.group(1)
        for lm in LINK_RE.finditer(entry):
            s = lm.group(1).strip()
            if not slug_last_touched.get(s) or date_str > slug_last_touched[s]:
                slug_last_touched[s] = date_str

    issues = []
    for page in pages:
        slug = page.stem
        last_touched = slug_last_touched.get(slug)
        if not last_touched:
            continue
        try:
            post = _fm_load(page)
            last_updated = str(post.metadata.get("last_updated", ""))
        except Exception:
            continue
        if last_updated and last_updated < last_touched:
            issues.append({
                "slug": slug,
                "last_updated": last_updated,
                "last_touched": last_touched,
            })
    return issues


# ── Prompts sémantiques ──────────────────────────────────────────────────

MISSING_CONCEPTS_SYSTEM = (
    "Tu es un analyste de wiki. On te donne le contenu d'une page et la liste des slugs existants.\n"
    "Identifie les concepts IMPORTANTS mentionnés dans cette page qui n'ont pas de page dédiée "
    "(leur slug n'est pas dans la liste fournie).\n"
    "Critères : terme clé ou central à la compréhension de la page — pas un mot générique ni un terme mineur.\n"
    "Maximum 5 concepts.\n"
    "Réponds UNIQUEMENT en JSON strict :\n"
    '{"missing_concepts": ["Nom du concept 1", "Nom du concept 2"]}'
)

CONTRADICTION_SYSTEM = (
    "Tu es un vérificateur de cohérence de wiki. On te donne le contenu de deux pages liées.\n"
    "Détermine si elles contiennent des CONTRADICTIONS réelles : assertions sur le même sujet "
    "qui ne peuvent pas être vraies simultanément (pas de simples différences d'emphase ou de perspective).\n"
    "Réponds UNIQUEMENT en JSON strict :\n"
    '{"contradictory": false, "explanation": "raison courte ou null"}'
)

SOURCE_GAPS_SYSTEM = (
    "Tu es un expert en gestion de connaissances. On te donne l'index d'un wiki personnel.\n"
    "Identifie exactement 5 lacunes thématiques : sujets effleurés mais non approfondis, "
    "perspectives manquantes, domaines connexes non couverts.\n"
    "Réponds UNIQUEMENT en JSON strict :\n"
    '{"gaps": [{"description": "...", "source_type": "article", "keywords": "mots-clés de recherche"}]}'
)

STALE_CLAIMS_SYSTEM = (
    "Tu es un vérificateur de wiki. On te donne une page wiki avec la source la plus ancienne "
    "et la source la plus récente référencées dans cette page.\n"
    "Détermine si la page contient des affirmations basées sur l'ancienne source qui sont "
    "contredites ou clairement dépassées par la source récente.\n"
    "Réponds UNIQUEMENT en JSON strict :\n"
    '{"stale": false, "explanation": "description courte ou null", "suggestion": "action recommandée ou null"}'
)


# ── Fonctions de lint sémantique ──────────────────────────────────────────

def _lint_missing_concepts(
    pages: list[Path], ctx: WikiContext, cache: dict
) -> list[dict]:
    """Identifie les concepts importants mentionnés dans le wiki sans page propre."""
    from collections import defaultdict as _defaultdict
    known_slugs = {p.stem for p in pages}
    analysis_pages = [p for p in pages if "sources" not in p.parts]

    concept_counts: Counter = Counter()
    concept_page_refs: dict[str, list[str]] = _defaultdict(list)

    for page in analysis_pages:
        try:
            content = page.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        cache_key = f"mc:{page.stem}:{_content_hash(content)}"
        result = _cache_get(cache, cache_key)

        if result is None:
            slugs_sample = ", ".join(sorted(known_slugs)[:80])
            try:
                result = llm_json(
                    MISSING_CONCEPTS_SYSTEM,
                    f"Page : {page.stem}\n\nContenu :\n{content[:3000]}\n\nSlugs existants : {slugs_sample}",
                    retries=1,
                )
            except Exception:
                result = {"missing_concepts": []}
            _cache_set(cache, cache_key, result)

        for name in result.get("missing_concepts", []):
            s = slugify(name)
            if s and len(s) > 2 and s not in known_slugs:
                concept_counts[name] += 1
                if page.stem not in concept_page_refs[name]:
                    concept_page_refs[name].append(page.stem)

    return [
        {"name": name, "slug": slugify(name), "count": count, "pages": concept_page_refs[name]}
        for name, count in concept_counts.most_common(10)
        if slugify(name) not in known_slugs
    ]


def _lint_contradictions(
    pages: list[Path], ctx: WikiContext, cache: dict, max_pairs: int = 20
) -> list[dict]:
    """Détecte les contradictions factuelles entre pages liées par des wikilinks."""
    slug_to_path = {p.stem: p for p in pages}
    pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for page in pages:
        if len(pairs) >= max_pairs:
            break
        try:
            content = page.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in LINK_RE.finditer(content):
            t = m.group(1).strip()
            if t in slug_to_path and t != page.stem:
                key = tuple(sorted([page.stem, t]))
                if key not in seen_pairs:
                    seen_pairs.add(key)
                    pairs.append(key)
                    if len(pairs) >= max_pairs:
                        break

    contradictions = []
    for slug_a, slug_b in pairs:
        try:
            ca = slug_to_path[slug_a].read_text(encoding="utf-8", errors="replace")
            cb = slug_to_path[slug_b].read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        cache_key = f"ctr:{slug_a}:{slug_b}:{_content_hash(ca, cb)}"
        result = _cache_get(cache, cache_key)

        if result is None:
            try:
                result = llm_json(
                    CONTRADICTION_SYSTEM,
                    f"Page A : [[{slug_a}]]\n\n{ca[:2000]}\n\n---\n\nPage B : [[{slug_b}]]\n\n{cb[:2000]}",
                    retries=1,
                )
            except Exception:
                result = {"contradictory": False, "explanation": None}
            _cache_set(cache, cache_key, result)

        if result.get("contradictory"):
            contradictions.append({
                "page_a": slug_a,
                "page_b": slug_b,
                "explanation": result.get("explanation", ""),
            })

    return contradictions


def _lint_source_gaps(ctx: WikiContext, cache: dict) -> list[dict]:
    """Identifie les lacunes thématiques du wiki à partir de son index."""
    index = read_index(ctx)
    if not index.strip() or "## Sources" not in index:
        return []

    cache_key = f"gaps:{_content_hash(index)}"
    result = _cache_get(cache, cache_key)

    if result is None:
        try:
            result = llm_json(
                SOURCE_GAPS_SYSTEM,
                f"Index du wiki :\n\n{index[:4000]}",
                retries=1,
            )
        except Exception:
            result = {"gaps": []}
        _cache_set(cache, cache_key, result)

    return result.get("gaps", [])


def _lint_stale_claims(
    pages: list[Path], ctx: WikiContext, cache: dict
) -> list[dict]:
    """Détecte les affirmations possiblement obsolètes dans les pages multi-sources."""
    slug_to_path = {p.stem: p for p in pages}
    stale_pages = []

    for page in pages:
        if "sources" in page.parts:
            continue
        try:
            post = _fm_load(page)
            raw_sources = post.metadata.get("sources", []) or []
        except Exception:
            continue

        source_slugs = []
        for s in raw_sources:
            raw = str(s)
            m = LINK_RE.match(raw)
            slug = m.group(1).strip() if m else raw.strip("[]")
            if slug in slug_to_path:
                source_slugs.append(slug)

        if len(source_slugs) < 2:
            continue

        def _get_ingested(s: str) -> str:
            try:
                return str(_fm_load(slug_to_path[s]).metadata.get("ingested", ""))
            except Exception:
                return ""

        sorted_srcs = sorted(source_slugs, key=_get_ingested)
        oldest, newest = sorted_srcs[0], sorted_srcs[-1]
        if oldest == newest:
            continue

        try:
            page_content = page.read_text(encoding="utf-8", errors="replace")
            oldest_c = slug_to_path[oldest].read_text(encoding="utf-8", errors="replace")
            newest_c = slug_to_path[newest].read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        cache_key = f"stale:{page.stem}:{_content_hash(page_content, oldest_c, newest_c)}"
        result = _cache_get(cache, cache_key)

        if result is None:
            try:
                result = llm_json(
                    STALE_CLAIMS_SYSTEM,
                    (
                        f"Page : [[{page.stem}]]\n\n{page_content[:1500]}\n\n"
                        f"---\nSource ancienne : [[{oldest}]]\n\n{oldest_c[:1000]}\n\n"
                        f"---\nSource récente : [[{newest}]]\n\n{newest_c[:1000]}"
                    ),
                    retries=1,
                )
            except Exception:
                result = {"stale": False}
            _cache_set(cache, cache_key, result)

        if result.get("stale"):
            stale_pages.append({
                "slug": page.stem,
                "explanation": result.get("explanation", ""),
                "suggestion": result.get("suggestion") or f"Mettre à jour [[{page.stem}]] avec [[{newest}]]",
            })

    return stale_pages


def _semantic_lint_estimate(pages: list[Path], ctx: WikiContext, cache: dict) -> int:
    """Estime le nombre d'appels LLM pour le lint sémantique (hors entrées déjà en cache)."""
    non_sources = [p for p in pages if "sources" not in p.parts]

    mc_calls = 0
    for p in non_sources:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if _cache_get(cache, f"mc:{p.stem}:{_content_hash(content)}") is None:
            mc_calls += 1

    slug_to_path = {p.stem: p for p in pages}
    seen: set[tuple[str, str]] = set()
    for pg in pages:
        if len(seen) >= 20:
            break
        try:
            content = pg.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in LINK_RE.finditer(content):
            t = m.group(1).strip()
            if t in slug_to_path and t != pg.stem:
                key = tuple(sorted([pg.stem, t]))
                if key not in seen and len(seen) < 20:
                    seen.add(key)
    ctr_calls = len(seen)

    index = read_index(ctx)
    gap_calls = 0 if _cache_get(cache, f"gaps:{_content_hash(index)}") else 1

    stale_calls = 0
    for pg in non_sources:
        try:
            post = _fm_load(pg)
            if len(post.metadata.get("sources", []) or []) >= 2:
                stale_calls += 1
        except Exception:
            pass

    return mc_calls + ctr_calls + gap_calls + stale_calls


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
@click.pass_context
def search(ctx, terms: tuple[str, ...], top: int) -> None:
    """Recherche full-text dans les pages wiki (BM25)."""
    pages = list_wiki_pages(ctx.obj["wiki_ctx"])
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
@click.pass_context
def stats(ctx) -> None:
    """Affiche des statistiques sur l'état du wiki."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    pages = list_wiki_pages(wiki_ctx)

    sources = [p for p in pages if "sources" in p.parts]
    entities = [p for p in pages if "entities" in p.parts]
    concepts = [p for p in pages if "concepts" in p.parts]
    analyses = [p for p in pages if "analyses" in p.parts]
    other = [
        p for p in pages
        if not any(d in p.parts for d in ("sources", "entities", "concepts", "analyses"))
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
            post = _fm_load(p)
            for tag in post.metadata.get("tags", []):
                tags_counter[tag] += 1
            kind = post.metadata.get("kind")
            if kind:
                kinds_counter[kind] += 1
        except Exception:
            pass

    raw_files = [f for f in wiki_ctx.raw_dir.rglob("*") if f.is_file()]

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
    if analyses:
        click.echo(f"    analyses/               : {len(analyses)}")
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

    log_file = wiki_ctx.log_file
    if log_file.exists():
        log_text = log_file.read_text(encoding="utf-8", errors="replace")
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
@click.pass_context
def batch_ingest(ctx, directory: Optional[Path], dry_run: bool, ext: str) -> None:
    """Ingère tous les fichiers d'un dossier (défaut: raw/)."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    corpus_id = ctx.obj.get("corpus_id")
    target = (directory or wiki_ctx.raw_dir).resolve()
    extensions = {e.strip().lower() for e in ext.split(",")}
    extensions = {e if e.startswith(".") else f".{e}" for e in extensions}

    candidates: list[Path] = []
    for f in sorted(target.rglob("*")):
        if f.is_file() and f.suffix.lower() in extensions:
            candidates.append(f)

    if not candidates:
        click.echo(f"[batch-ingest] aucun fichier trouvé dans {target} (extensions: {extensions})")
        return

    already_ingested: set[str] = set()
    sources_dir = wiki_ctx.wiki_dir / "sources"
    if sources_dir.exists():
        for sp in sources_dir.glob("*.md"):
            try:
                post = _fm_load(sp)
                src = post.metadata.get("source_path", "")
                if src:
                    already_ingested.add(src)
            except Exception:
                pass

    to_ingest: list[Path] = []
    skipped: list[Path] = []
    for f in candidates:
        try:
            rel = str(f.relative_to(wiki_ctx.raw_dir.resolve())).replace("\\", "/")
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

    success = 0
    errors: list[tuple[Path, str]] = []
    for i, f in enumerate(to_ingest, 1):
        click.echo(f"\n{'='*50}")
        click.echo(f"[batch-ingest] {i}/{len(to_ingest)} : {f.name}")
        click.echo(f"{'='*50}")
        try:
            args = ["ingest", str(f), "--batch"]
            if corpus_id:
                args = ["--corpus", corpus_id] + args
            cli.main(args, standalone_mode=False)
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
@click.pass_context
def export(ctx, slug: str, fmt: str, output: Optional[Path]) -> None:
    """Exporte une page wiki en HTML standalone ou en slides Marp."""
    pages = list_wiki_pages(ctx.obj["wiki_ctx"])
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
@click.pass_context
def watch(ctx, interval: int, ext: str) -> None:
    """Surveille raw/ et ingère automatiquement les nouveaux fichiers."""
    import time

    wiki_ctx = ctx.obj["wiki_ctx"]
    corpus_id = ctx.obj.get("corpus_id")
    extensions = {e.strip() if e.startswith(".") else f".{e}" for e in ext.split(",")}

    click.echo(f"[watch] surveillance de {wiki_ctx.raw_dir}")
    click.echo(f"  extensions : {extensions}")
    click.echo(f"  intervalle : {interval}s")
    click.echo(f"  Ctrl+C pour arrêter.\n")

    _get_llm()

    def _already_ingested() -> set[str]:
        done: set[str] = set()
        sources_dir = wiki_ctx.wiki_dir / "sources"
        if sources_dir.exists():
            for sp in sources_dir.glob("*.md"):
                try:
                    post = _fm_load(sp)
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
            for f in sorted(wiki_ctx.raw_dir.rglob("*")):
                if not f.is_file() or f.suffix.lower() not in extensions:
                    continue
                try:
                    rel = str(f.relative_to(wiki_ctx.raw_dir.resolve())).replace("\\", "/")
                except ValueError:
                    continue
                if rel in seen:
                    continue

                click.echo(f"\n[watch] nouveau fichier : {rel}")
                try:
                    args = ["ingest", str(f), "--batch"]
                    if corpus_id:
                        args = ["--corpus", corpus_id] + args
                    cli.main(args, standalone_mode=False)
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
@click.pass_context
def graph(ctx, output: Optional[Path]) -> None:
    """Génère un graphe interactif des relations du wiki (HTML + d3.js)."""
    pages = list_wiki_pages(ctx.obj["wiki_ctx"])
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
            post = _fm_load(p)
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
@click.pass_context
def suggest(ctx, deep: bool) -> None:
    """Le LLM analyse le wiki et propose des pistes d'exploration."""
    wiki_ctx = ctx.obj["wiki_ctx"]
    index = read_index(wiki_ctx)
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
        pages = list_wiki_pages(wiki_ctx)
        budget = token_budget() - estimate_tokens(SUGGEST_DEEP_SYSTEM) - 500
        loaded: list[str] = []
        tokens_used = 0

        priority = sorted(
            pages,
            key=lambda p: (0 if "concepts" in p.parts else 1 if "entities" in p.parts else 2),
        )
        for p in priority:
            content = p.read_text(encoding="utf-8", errors="replace")
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

    append_log("note", f"suggest {'deep' if deep else 'light'}", "- Suggestions affichées.\n", wiki_ctx)


# ---------------------------------------------------------------------------
# Commande : shell — REPL interactif (toutes les commandes)
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def shell(ctx) -> None:
    """Mode interactif : charge le modèle une fois, accepte des commandes en boucle."""
    corpus_id = ctx.obj.get("corpus_id")

    def _run(*args):
        """Exécute une commande CLI en propageant le corpus actif."""
        full_args = list(args)
        if corpus_id:
            full_args = ["--corpus", corpus_id] + full_args
        cli.main(full_args, standalone_mode=False)

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
                _run("ingest", str(path), "--batch")

            elif cmd == "query":
                if not arg:
                    click.echo("  Usage : query <question> [--file-back]")
                    continue
                fb = "--file-back" in arg
                q = arg.replace("--file-back", "").strip()
                a = ["query", q]
                if fb:
                    a.append("--file-back")
                _run(*a)

            elif cmd == "search":
                if not arg:
                    click.echo("  Usage : search <termes>")
                    continue
                _run("search", *arg.split())

            elif cmd == "lint":
                la = ["lint"]
                if "--auto-fix" in arg:
                    la.append("--auto-fix")
                elif "--fix-index" in arg:
                    la.append("--fix-index")
                _run(*la)

            elif cmd == "overview":
                _run("overview")

            elif cmd == "stats":
                _run("stats")

            elif cmd == "export":
                if not arg:
                    click.echo("  Usage : export <slug> [--format html|marp]")
                    continue
                _run("export", *arg.split())

            elif cmd in ("batch-ingest", "batch"):
                ba = ["batch-ingest"]
                if arg:
                    ba += arg.split()
                _run(*ba)

            elif cmd == "graph":
                _run("graph")

            elif cmd == "suggest":
                sa = ["suggest"]
                if "--deep" in arg:
                    sa.append("--deep")
                _run(*sa)

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
