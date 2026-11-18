"""Local quantized ONNX cross-encoder and zero-dependency heuristic drivers."""

from __future__ import annotations

import asyncio
import inspect
import math
import os
import re
import tempfile
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

from quadgate.contracts import ClassifierHead, ClassifierRequest, HeadCategory
from quadgate.drivers.base import CalibrationProfile, ClassifierDriver, DriverCapabilities

DEFAULT_LOCAL_MODEL_URL = (
    "https://huggingface.co/quadgate/modernbert-reranker-int8/resolve/main/model_quantized.onnx"
)
DEFAULT_LOCAL_TOKENIZER_URL = (
    "https://huggingface.co/quadgate/modernbert-reranker-int8/resolve/main/tokenizer.json"
)
_INSTALL_HINT = "pip install quadgate[local] && quadgate init --local"

_TOKEN_SPLIT_RE = re.compile(r"[A-Za-z0-9_]+")
_CAMEL_BOUNDARY_1_RE = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_BOUNDARY_2_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_HYPHEN_COMPOUND_RE = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
_COMMA_NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
_DECIMAL_NUM_RE = re.compile(r"\b\d+\.\d+\b")
_WIKILINK_RE = re.compile(
    r"\[\[([^\[\]|#\n]{1,256})(?:#[^\[\]|\n]{0,256})?(?:\|[^\[\]\n]{0,256})?\]\]"
)
_PATH_OR_FILE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?>(?:[A-Za-z0-9_.-]{1,128}[\\/]){0,12})"
    r"[A-Za-z0-9_-]{1,128}\.[A-Za-z][A-Za-z0-9]{0,7}"
)
_SNAKE_OR_CAMEL_ID_RE = re.compile(
    r"\b(?:[a-z]+_[a-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]+)+)\b"
)
_KNOWN_FILE_EXTS: frozenset[str] = frozenset(
    {
        "py",
        "pyi",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "rs",
        "go",
        "md",
        "markdown",
        "mdx",
        "txt",
        "rst",
        "csv",
        "tsv",
        "sql",
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "xml",
        "html",
        "css",
        "scss",
        "sh",
        "bash",
        "ps1",
        "c",
        "h",
        "cc",
        "cpp",
        "hpp",
        "java",
        "kt",
        "rb",
        "php",
        "swift",
        "r",
        "env",
        "log",
        "pdf",
        "doc",
        "docx",
        "onnx",
        "db",
        "sqlite",
        "lock",
        "mod",
        "sum",
    }
)

_COUNTERFACTUAL_HEAD_RE = re.compile(
    r"\b(?:counterfactual|hypothetical|non[-\s]?directive|brainstorm|read[-\s]?only|"
    r"should\s+not\s+mutate|speculative|exploratory|exploration)\b",
    re.IGNORECASE,
)
_DIRECTIVE_HEAD_RE = re.compile(
    r"\b(?:directive|actionable\s+instruction|mutation\s+instruction|execute\s+edit)\b",
    re.IGNORECASE,
)
_SECRET_HEAD_RE = re.compile(
    r"\b(?:exfiltrat\w*|secret\w*|credential\w*|api[_\s]?key\w*|private[_\s]?key\w*|"
    r"private\s+vault|health\s+records?|sensitive\s+environment|pii|ssns?)\b|\.env\b",
    re.IGNORECASE,
)
_CAGE_HEAD_RE = re.compile(
    r"\b(?:cage|containment|assigned\s+file|path\s+escape|directory\s+traversal|"
    r"workspace\s+boundary|outside\s+workspace)\b",
    re.IGNORECASE,
)

_CF_STRONG_CUES_RE = re.compile(
    r"\b(?:what\s+if|suppose|hypothetically|imagine\s+if|in\s+theory|were\s+we\s+to|"
    r"if\s+we\s+were\s+to|brainstorm|just\s+wondering|thoughts\s+on|pros\s+and\s+cons|"
    r"what\s+would\s+happen|could\s+we\s+ever|would\s+it\s+make\s+sense)\b",
    re.IGNORECASE,
)
_CF_TEMPORAL_CUES_RE = re.compile(
    r"\b(?:eventually|someday|next\s+year|next\s+quarter|in\s+the\s+future|"
    r"down\s+the\s+road|maybe\s+later|one\s+day|long\s+term)\b",
    re.IGNORECASE,
)
_IMPERATIVE_START_RE = re.compile(
    r"^\s*(?:please\s+)?(?:fix|update|edit|modify|add|remove|delete|implement|refactor|"
    r"create|write|patch|run|execute|deploy|install|change|migrate|rename|move)\b",
    re.IGNORECASE,
)
_URGENCY_CUES_RE = re.compile(
    r"\b(?:right\s+now|immediately|asap|urgent|this\s+pr|go\s+ahead\s+and)\b",
    re.IGNORECASE,
)
_DIRECTIVE_VERB_RE = re.compile(
    r"\b(?:fix|update|edit|modify|add|remove|delete|implement|refactor|create|write|patch|"
    r"run|execute|deploy|install|change)\b",
    re.IGNORECASE,
)

_DOTENV_SECRET_RE = re.compile(
    r"(?:^|[\s/\\\"'@=])\.env(?:\.(?!example\b|sample\b|template\b)[a-z0-9_-]+)?(?=$|[\s\"'&;|])",
    re.IGNORECASE,
)
_EXFIL_TOOL_RE = re.compile(
    r"\b(?:curl|wget|invoke-webrequest|invoke-restmethod|nc|scp|webhook\.site)\b|"
    r"(?:-d\s+@|--data(?:-binary|-urlencode)?\s+@|-X\s+POST)",
    re.IGNORECASE,
)
_SENSITIVE_PATH_OR_DUMP_RE = re.compile(
    r"(?:~/\.ssh|~/\.aws|/etc/shadow|\bid_rsa\b|\bid_ed25519\b|\bprintenv\b|"
    r"\b(?:cat|source)\s+[^\n]{0,240}\.env)",
    re.IGNORECASE,
)
_HARDCODED_SECRET_RE = re.compile(
    r"-----BEGIN\s++(?:(?!PRIVATE\s++KEY\b)[A-Z0-9]++\s++)*+PRIVATE\s++KEY(?:\s++BLOCK)?-----|"
    r"(?<![A-Za-z0-9_-])(?:"
    r"(?>AKIA[0-9A-Z]{16})|"
    r"(?>sk-[A-Za-z0-9_-]{20,})|"
    r"(?>gh[pousr]_[A-Za-z0-9]{20,})|"
    r"(?>github_pat_[A-Za-z0-9_]{22,})|"
    r"(?>AIza[0-9A-Za-z_-]{35})|"
    r"(?>AQ\.[A-Za-z0-9_-]{30,})"
    r")(?![A-Za-z0-9_-])|"
    r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
)
_AIRGAP_TAG_RE = re.compile(
    r"\[(?:private|confidential|airgap|tier1-private)\]",
    re.IGNORECASE,
)
_PATH_ESCAPE_RE = re.compile(r"(?:\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows|~/\.ssh)", re.I)
_HIGH_COMPLEXITY_RE = re.compile(
    r"\b(?:architect(?:ure)?|distributed|consensus|concurrency|race\s+condition|"
    r"deadlock|cryptograph\w*|security\s+vulnerabilit\w*|zero[-\s]?downtime|"
    r"multi[-\s]?(?:service|region|module)|kernel|compiler|scheduler|migration)\b",
    re.IGNORECASE,
)
_LOW_COMPLEXITY_RE = re.compile(
    r"\b(?:boilerplate|docstring|comment|typo|rename|format|lint|readme|changelog|"
    r"extract\s+table|unit\s+test|stub|type\s+hint|helper)\b",
    re.IGNORECASE,
)

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

_CLAUSE_META_WORDS: frozenset[str] = frozenset(
    {
        "across",
        "add",
        "all",
        "also",
        "any",
        "both",
        "build",
        "can",
        "clause",
        "clauses",
        "could",
        "cover",
        "covering",
        "create",
        "document",
        "draft",
        "each",
        "edit",
        "ensure",
        "ensuring",
        "every",
        "file",
        "files",
        "fix",
        "implement",
        "include",
        "legal",
        "make",
        "may",
        "modify",
        "must",
        "only",
        "patch",
        "per",
        "please",
        "produce",
        "refactor",
        "report",
        "rewrite",
        "run",
        "should",
        "so",
        "specify",
        "state",
        "synthesize",
        "synthesizing",
        "then",
        "update",
        "use",
        "using",
        "via",
        "when",
        "where",
        "would",
        "write",
    }
)


def _stem_token(token: str) -> str:
    """Apply lightweight deterministic suffix normalization for lexical matching."""
    n = len(token)
    if n < 5:
        return token
    for suffix, repl in (
        ("ifications", "if"),
        ("ification", "if"),
        ("ifying", "if"),
        ("ified", "if"),
        ("izations", "iz"),
        ("ization", "iz"),
        ("izing", "iz"),
        ("ized", "iz"),
        ("ations", ""),
        ("ation", ""),
        ("ators", ""),
        ("ator", ""),
        ("ating", ""),
        ("ated", ""),
        ("ates", ""),
        ("ate", ""),
        ("ments", "ment"),
        ("ness", ""),
        ("ing", ""),
        ("ies", "y"),
        ("ied", "y"),
    ):
        min_rem = 4 if repl == "" and suffix.startswith("at") else 3
        if token.endswith(suffix) and len(token) - len(suffix) + len(repl) >= min_rem:
            res = token[: -len(suffix)] + repl
            return "verif" if res in ("verify", "verifi") else res
    if token.endswith("tions") and n >= 7:
        return token[:-4]
    if token.endswith("tion") and n >= 6:
        return token[:-3]
    if token.endswith("ed") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("es") and n - 2 >= 4 and not token.endswith("ss"):
        return token[:-2]
    if token.endswith("er") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("or") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("ly") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("s") and n - 1 >= 4 and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    if token.endswith("y") and n - 1 >= 4:
        return token[:-1] + "i"
    if token.endswith("e") and n >= 5 and not token.endswith("ee"):
        return token[:-1]
    return token


_CLAUSE_META_STEMS: frozenset[str] = _CLAUSE_META_WORDS | frozenset(
    _stem_token(w) for w in _CLAUSE_META_WORDS
)


def _is_recognized_file_ref(raw_path: str) -> bool:
    """Return True when `raw_path` represents a file path or known filename extension."""
    if "/" in raw_path or "\\" in raw_path:
        return True
    if "." not in raw_path:
        return False
    return raw_path.rsplit(".", 1)[-1].lower() in _KNOWN_FILE_EXTS


def _extract_lexical_features(
    text: str,
) -> tuple[list[str], set[str], set[str], set[str], set[str], set[str]]:
    """Extract normalized tokens, identifiers, file paths, basenames, stems, and wikilinks."""
    stemmed_tokens: list[str] = []
    identifiers: set[str] = set()
    paths: set[str] = set()
    basenames: set[str] = set()
    stems: set[str] = set()
    wikilinks: set[str] = set()

    if "[[" in text:
        for match in _WIKILINK_RE.finditer(text):
            raw_wiki = match.group(1).strip().replace("\\", "/").lstrip("./").lower()
            if raw_wiki:
                w_stem = raw_wiki.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if w_stem:
                    wikilinks.add(w_stem)
                    stems.add(w_stem)
                    identifiers.add(w_stem)

    if "." in text:
        for match in _PATH_OR_FILE_RE.finditer(text):
            raw_path = match.group(0).replace("\\", "/").lstrip("./").lower()
            if not _is_recognized_file_ref(raw_path):
                continue
            paths.add(raw_path)
            identifiers.add(raw_path)
            basename = raw_path.rsplit("/", 1)[-1]
            basenames.add(basename)
            identifiers.add(basename)
            stem = basename.rsplit(".", 1)[0]
            if stem and not stem.startswith("."):
                stems.add(stem)
                identifiers.add(stem)

    if "," in text:
        for match in _COMMA_NUM_RE.finditer(text):
            identifiers.add(match.group(0).replace(",", ""))
    if "." in text:
        for match in _DECIMAL_NUM_RE.finditer(text):
            identifiers.add(match.group(0))

    if "-" in text:
        for match in _HYPHEN_COMPOUND_RE.finditer(text):
            span = match.group(0)
            if any(ch.isalpha() for ch in span):
                joined = span.replace("-", "").lower()
                if 4 <= len(joined) <= 28:
                    identifiers.add(joined)

    for match in _SNAKE_OR_CAMEL_ID_RE.finditer(text):
        ident = match.group(0).lower()
        if len(ident) >= 3:
            identifiers.add(ident)

    norm_text = (
        _COMMA_NUM_RE.sub(lambda m: m.group(0).replace(",", ""), text) if "," in text else text
    )
    for chunk in _TOKEN_SPLIT_RE.findall(norm_text):
        # Expand snake_case segments first, then camelCase/PascalCase boundaries.
        for snake_part in chunk.split("_"):
            if not snake_part:
                continue
            expanded = _CAMEL_BOUNDARY_2_RE.sub(r"\1 \2", snake_part)
            expanded = _CAMEL_BOUNDARY_1_RE.sub(r"\1 \2", expanded)
            for sub in expanded.split():
                low = sub.lower()
                if not low or low in _STOPWORDS:
                    continue
                if len(low) == 1 and not low.isdigit():
                    continue
                stemmed = _stem_token(low)
                if stemmed == "verify" or stemmed == "verifi":
                    stemmed = "verif"
                stemmed_tokens.append(stemmed)
                if len(low) >= 3 or low.isdigit():
                    identifiers.add(low)

    return stemmed_tokens, identifiers, paths, basenames, stems, wikilinks


def _extract_tokens_and_identifiers(text: str) -> tuple[list[str], set[str], set[str]]:
    """Split paths, camelCase, PascalCase, and snake_case into normalized tokens."""
    stemmed_tokens, identifiers, _, _, stems, _ = _extract_lexical_features(text)
    return stemmed_tokens, identifiers, stems


def _extract_candidate_identity(head: ClassifierHead) -> tuple[str | None, str | None, str | None]:
    """Return normalized `(own_path, own_basename, own_stem)` for a file candidate head."""
    rel = head.metadata.get("rel_path")
    own_path: str | None = None
    if isinstance(rel, str) and rel.strip():
        own_path = rel.strip().replace("\\", "/").lstrip("./").lower()
    else:
        cand = head.candidate_text.strip()
        prefix = cand.split(":", 1)[0].strip() if ":" in cand else cand
        if (
            " " not in prefix
            and _PATH_OR_FILE_RE.fullmatch(prefix)
            and _is_recognized_file_ref(prefix)
        ):
            own_path = prefix.replace("\\", "/").lstrip("./").lower()
    if not own_path:
        return None, None, None
    own_base = own_path.rsplit("/", 1)[-1]
    has_ext = "." in own_base and not own_base.startswith(".")
    own_stem = own_base.rsplit(".", 1)[0] if has_ext else own_base
    return own_path, own_base, own_stem


def _classify_head_role(head: ClassifierHead) -> str:
    """Identify whether a head evaluates a pragmatic, invariant, clause, or candidate signal."""
    hid = head.head_id.lower()
    head_type = str(head.metadata.get("head_type", "")).lower()
    cand = head.candidate_text
    combined_id = f"{hid} {head_type}"
    if hid.startswith("clause:") or "clause_index" in head.metadata:
        return "clause"
    if hid == "tier:floor" or head_type == "tier:floor":
        return "tier_floor"
    if hid == "tier:mid" or head_type == "tier:mid":
        return "tier_mid"
    if hid == "tier:frontier" or head_type == "tier:frontier":
        return "tier_frontier"
    if head.category == HeadCategory.CANDIDATE and (
        "rel_path" in head.metadata or _extract_candidate_identity(head)[0] is not None
    ):
        return "candidate"
    if (
        "counterfactual" in combined_id
        or "brainstorm" in combined_id
        or _COUNTERFACTUAL_HEAD_RE.search(cand)
    ):
        return "counterfactual"
    if _DIRECTIVE_HEAD_RE.search(cand):
        return "directive"
    if (
        "inv_01" in combined_id
        or "secret" in combined_id
        or "pii_airgap" in combined_id
        or _SECRET_HEAD_RE.search(cand)
    ):
        return "inv_secret"
    if "inv_02" in combined_id or "cage" in combined_id or _CAGE_HEAD_RE.search(cand):
        return "inv_cage"
    if head.category == HeadCategory.INVARIANT:
        return "inv_other"
    if head.category == HeadCategory.PRAGMATIC and "non" in cand.lower():
        return "counterfactual"
    return "candidate"


def _score_counterfactual_query(query: str, lexical_score: float) -> float:
    """Score non-directive counterfactual/brainstorm intent in [0.0, 1.0]."""
    score = 0.14 + 0.10 * lexical_score
    strong_hits = len({m.group(0).lower() for m in _CF_STRONG_CUES_RE.finditer(query)})
    temporal_hits = len({m.group(0).lower() for m in _CF_TEMPORAL_CUES_RE.finditer(query)})
    if strong_hits:
        score += min(0.62, 0.44 + 0.14 * (strong_hits - 1))
    if temporal_hits:
        score += min(0.36, 0.24 + 0.10 * (temporal_hits - 1))
    if query.rstrip().endswith("?"):
        score += 0.14

    if _URGENCY_CUES_RE.search(query):
        score -= 0.34
    if _IMPERATIVE_START_RE.search(query) and strong_hits == 0:
        score -= 0.30
    elif _DIRECTIVE_VERB_RE.search(query) and strong_hits == 0 and temporal_hits == 0:
        score -= 0.16

    return min(0.96, max(0.02, score))


def _score_secret_invariant_query(query: str, lexical_score: float) -> float:
    """Score secret exposure / credential exfiltration cues in [0.0, 1.0]."""
    score = 0.04 + 0.08 * lexical_score
    if _HARDCODED_SECRET_RE.search(query):
        score += 0.78
    if _DOTENV_SECRET_RE.search(query):
        score += 0.46
    if _EXFIL_TOOL_RE.search(query):
        score += 0.34
    if _SENSITIVE_PATH_OR_DUMP_RE.search(query):
        score += 0.36
    if _AIRGAP_TAG_RE.search(query):
        score += 0.44
    return min(0.98, max(0.02, score))


def _score_cage_invariant_query(query: str, lexical_score: float) -> float:
    """Score workspace cage containment violation cues in [0.0, 1.0]."""
    score = 0.05 + 0.15 * lexical_score
    if _PATH_ESCAPE_RE.search(query):
        score += 0.80
    return min(0.97, max(0.02, score))


def _score_tier_priors(query: str, q_paths: set[str]) -> dict[str, float]:
    """Return offline Gate 3 subagent pass priors `(tier_floor, tier_mid, tier_frontier)`."""
    has_high = bool(_HIGH_COMPLEXITY_RE.search(query))
    has_low = bool(_LOW_COMPLEXITY_RE.search(query))
    word_count = len(query.split())
    many_files = len(q_paths) >= 4
    if has_high or (word_count > 65 and not has_low) or many_files:
        return {"tier_floor": 0.42, "tier_mid": 0.56, "tier_frontier": 0.96}
    if word_count > 35 and not has_low:
        return {"tier_floor": 0.70, "tier_mid": 0.86, "tier_frontier": 0.97}
    return {"tier_floor": 0.90, "tier_mid": 0.95, "tier_frontier": 0.99}


def _prepare_clause_evidence(
    evidence_text: str,
) -> tuple[set[str], set[str], set[str]]:
    """Precompute evidence token set, 5-char prefix set, and identifier set for Gate 4 clauses."""
    e_tokens, e_idents, _, _, e_stems, e_wikis = _extract_lexical_features(evidence_text)
    e_tok_set = set(e_tokens)
    if e_tok_set & {"def", "fn", "func"}:
        e_tok_set.update({"function", "method", "helper"})
    if e_tok_set & {"class", "struct"}:
        e_tok_set.update({"type", "class"})
    if e_tok_set & {
        "rais",
        "except",
        "valueerror",
        "typeerror",
        "runtimeerror",
        "permissionerror",
        "keyerror",
    }:
        e_tok_set.update({"error", "exception", "reject", "forbid", "block"})
    if "redact" in e_tok_set:
        e_tok_set.add("redacted")
    if e_stems:
        e_tok_set.update({"file", "module"})
    e_prefixes5 = {et[:5] for et in e_tok_set if len(et) >= 5}
    e_all_idents = e_idents | e_tok_set | e_stems | e_wikis
    return e_tok_set, e_prefixes5, e_all_idents


def _score_clause_addressed(
    clause_text: str,
    e_tok_set: set[str],
    e_prefixes5: set[str],
    e_all_idents: set[str],
) -> float:
    """Score whether a requirement clause is addressed by the provided evidence artifact."""
    if not e_tok_set and not e_all_idents:
        return 0.0
    c_tokens, c_idents, _, _, c_stems, c_wikis = _extract_lexical_features(clause_text)

    content_tokens = [tok for tok in c_tokens if tok not in _CLAUSE_META_STEMS]
    key_tokens = content_tokens if content_tokens else c_tokens
    if not key_tokens:
        return 0.95

    hits = sum(
        1
        for tok in key_tokens
        if tok in e_tok_set or (len(tok) >= 5 and tok[:5] in e_prefixes5)
    )
    tok_recall = hits / len(key_tokens)

    if c_wikis and not (c_wikis <= e_all_idents):
        return min(0.25, tok_recall * 0.35)

    explicit_stems = c_stems | c_wikis
    stem_recall = 0.0
    if explicit_stems:
        stem_hits = sum(1 for st in explicit_stems if st in e_all_idents)
        stem_recall = stem_hits / len(explicit_stems)
        if stem_recall == 0.0:
            return min(0.38, tok_recall * 0.45)

    spec_idents = [
        ident
        for ident in c_idents
        if ("_" in ident or "." in ident or ident.isdigit()) and ident not in _CLAUSE_META_STEMS
    ]

    weighted_sum = 0.75 * tok_recall
    weight_total = 0.75
    if explicit_stems:
        weighted_sum += 0.25 * stem_recall
        weight_total += 0.25
    if spec_idents:
        ident_hits = sum(1 for idt in spec_idents if idt in e_all_idents)
        weighted_sum += 0.25 * (ident_hits / len(spec_idents))
        weight_total += 0.25

    combined = weighted_sum / weight_total
    if combined >= 0.78:
        return min(0.99, 0.86 + (0.13 / 0.22) * (combined - 0.78))
    return max(0.02, min(0.78, combined * 0.90))


class ZeroDepDriver(ClassifierDriver):
    """Lightweight deterministic TF-IDF + BM25 + structural heuristic fallback driver."""

    def __init__(self, calibration_profile: CalibrationProfile | None = None) -> None:
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "zero_dep"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=False,
            supports_parallel_heads=True,
            max_candidates_per_pass=256,
            scores_are_logits=False,
        )

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        """Score heads deterministically in [0.0, 1.0] with zero external I/O."""
        if len(request.heads) > self.capabilities.max_candidates_per_pass:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most "
                f"{self.capabilities.max_candidates_per_pass} heads per pass; "
                f"got {len(request.heads)}."
            )

        roles = [_classify_head_role(head) for head in request.heads]
        results: dict[str, float] = {}

        if "clause" in roles:
            e_tok_set, e_prefixes5, e_all_idents = _prepare_clause_evidence(request.query_text)
            for head, role in zip(request.heads, roles, strict=True):
                if role == "clause":
                    results[head.head_id] = float(
                        _score_clause_addressed(
                            head.candidate_text, e_tok_set, e_prefixes5, e_all_idents
                        )
                    )
            if len(results) == len(request.heads):
                return results

        q_tokens, q_idents, q_paths, q_bases, q_stems, q_wikis = _extract_lexical_features(
            request.query_text
        )
        if any(r.startswith("tier_") for r in roles):
            tier_priors = _score_tier_priors(request.query_text, q_paths)
            for head, role in zip(request.heads, roles, strict=True):
                if role in tier_priors:
                    results[head.head_id] = float(tier_priors[role])
            if len(results) == len(request.heads):
                return results

        q_counts = Counter(q_tokens)
        q_unique = list(q_counts.keys())
        q_tok_set = set(q_tokens)

        parsed_heads: list[
            tuple[
                ClassifierHead,
                str,
                str | None,
                str | None,
                str | None,
                list[str],
                set[str],
                set[str],
            ]
        ] = []
        df: Counter[str] = Counter()
        df_ident: Counter[str] = Counter()
        total_dl = 0
        n_cand_docs = 0

        for head, role in zip(request.heads, roles, strict=True):
            if role == "clause" or role.startswith("tier_"):
                continue
            own_path, own_base, own_stem = _extract_candidate_identity(head)
            if " — " in head.candidate_text:
                header_part, body_part = head.candidate_text.split(" — ", 1)
                h_tokens, h_idents, _, _, _, _ = _extract_lexical_features(header_part)
                b_tokens, b_idents, _, _, _, _ = _extract_lexical_features(body_part)
                c_tokens = h_tokens + b_tokens
                c_idents = h_idents | b_idents
                h_tok_set = set(h_tokens)
            else:
                c_tokens, c_idents, _, _, _, _ = _extract_lexical_features(head.candidate_text)
                h_tok_set = set(c_tokens)

            parsed_heads.append(
                (head, role, own_path, own_base, own_stem, c_tokens, c_idents, h_tok_set)
            )
            if role == "candidate":
                n_cand_docs += 1
                total_dl += len(c_tokens)
                for tok in set(c_tokens):
                    df[tok] += 1
                for ident in c_idents:
                    df_ident[ident] += 1

        if n_cand_docs == 0:
            for head, _, _, _, _, c_tokens, c_idents, _ in parsed_heads:
                total_dl += len(c_tokens)
                for tok in set(c_tokens):
                    df[tok] += 1
                for ident in c_idents:
                    df_ident[ident] += 1
            n_docs = len(parsed_heads)
        else:
            n_docs = n_cand_docs

        avg_dl = max(1.0, total_dl / max(1, n_docs))
        min_pos_idf = math.log(1.0 + (n_docs + 1.0) / (n_docs + 0.5))

        # Smoothed positive IDF ensures valid weighting even when n_docs == 1.
        def _idf(tok: str) -> float:
            return math.log(1.0 + (n_docs + 1.0) / (df.get(tok, 0) + 0.5))

        q_weights: dict[str, float] = {}
        q_weights_full: dict[str, float] = {}
        for tok in q_unique:
            tf_q_factor = 1.0 + 0.25 * math.log1p(q_counts[tok] - 1)
            full_w = _idf(tok) * tf_q_factor
            q_weights_full[tok] = full_w
            if n_cand_docs >= 2 and df.get(tok, 0) == 0:
                q_weights[tok] = 0.25 * min_pos_idf * tf_q_factor
            else:
                q_weights[tok] = full_w

        q_norm_l2 = math.sqrt(sum(w * w for w in q_weights.values())) or 1.0
        q_weight_sum = sum(q_weights.values()) or 1.0
        q_full_norm_l2 = math.sqrt(sum(w * w for w in q_weights_full.values())) or 1.0
        q_full_weight_sum = sum(q_weights_full.values()) or 1.0
        matched_q_weight_sum = (
            sum(w for tok, w in q_weights.items() if df.get(tok, 0) > 0) or q_weight_sum
        )

        k1 = 1.2
        b = 0.55

        for head, role, own_path, own_base, own_stem, c_tokens, c_idents, h_tok_set in parsed_heads:
            c_counts = Counter(c_tokens)
            dl = len(c_tokens)
            dl_norm = 1.0 - b + b * (dl / avg_dl)
            use_cand_weights = n_cand_docs >= 2 and role == "candidate"
            active_q_weights = q_weights if use_cand_weights else q_weights_full
            active_q_norm_l2 = q_norm_l2 if use_cand_weights else q_full_norm_l2
            active_q_weight_sum = q_weight_sum if use_cand_weights else q_full_weight_sum

            bm25_acc = 0.0
            dot_product = 0.0
            if dl > 0 and q_unique:
                for tok in q_unique:
                    tf = c_counts.get(tok, 0)
                    if tf == 0:
                        continue
                    qw = active_q_weights[tok]
                    bm25_term = (tf * (k1 + 1.0)) / (tf + k1 * dl_norm)
                    bm25_acc += qw * min(1.15, bm25_term)
                    c_w = _idf(tok) * (1.0 + math.log1p(tf - 1))
                    dot_product += qw * c_w

                c_norm_l2 = math.sqrt(
                    sum((_idf(t) * (1.0 + math.log1p(cnt - 1))) ** 2 for t, cnt in c_counts.items())
                )
                tfidf_cos = (
                    dot_product / (active_q_norm_l2 * c_norm_l2) if c_norm_l2 > 0.0 else 0.0
                )
                bm25_all = min(1.0, bm25_acc / active_q_weight_sum)
                bm25_matched = min(1.0, bm25_acc / matched_q_weight_sum)
                bm25_norm = (
                    0.55 * bm25_all + 0.45 * bm25_matched
                    if use_cand_weights
                    else bm25_all
                )
            else:
                tfidf_cos = 0.0
                bm25_norm = 0.0

            if role == "inv_other":
                raw_inv = 0.50 * bm25_norm + 0.25 * tfidf_cos
                score = min(0.95, 1.0 - math.exp(-2.65 * raw_inv)) if raw_inv > 0.0 else 0.0
                results[head.head_id] = float(min(1.0, max(0.0, score)))
                continue

            path_boost = 0.0
            if role == "candidate":
                if own_path and own_path in q_paths:
                    path_boost = 0.50
                elif own_base and own_base in q_bases and not any(
                    qp.endswith("/" + own_base) and qp != own_path for qp in q_paths
                ):
                    path_boost = 0.42
                elif own_stem and (
                    own_stem in q_wikis
                    or (
                        own_stem in q_stems
                        and not any(
                            qp.rsplit("/", 1)[-1].rsplit(".", 1)[0] == own_stem and qp != own_path
                            for qp in q_paths
                        )
                    )
                ):
                    path_boost = 0.38
                elif own_stem:
                    own_stem_toks, *_ = _extract_lexical_features(own_stem.replace("_", " "))
                    max_df_stem = max(2, int(0.5 * n_docs))
                    disc_stem_toks = [
                        t
                        for t in own_stem_toks
                        if not t.isdigit() and (n_docs < 3 or df.get(t, 0) <= max_df_stem)
                    ]
                    if disc_stem_toks:
                        hits = sum(1 for t in disc_stem_toks if t in q_tok_set)
                        if hits >= 2 or (hits == len(disc_stem_toks) and not q_paths):
                            path_boost = min(0.20, 0.20 * (hits / len(disc_stem_toks)))

            ident_overlap = q_idents & c_idents
            if n_docs >= 3 and role == "candidate":
                log_denom = math.log(1.0 + n_docs)
                disc_ident_score = sum(
                    min(1.0, math.log(1.0 + n_docs / max(1, df_ident.get(ident, 1))) / log_denom)
                    for ident in ident_overlap
                )
                ident_boost = min(0.20, 0.045 * disc_ident_score)
            else:
                ident_boost = min(0.30, 0.07 * len(ident_overlap))

            header_boost = 0.0
            if n_docs >= 3 and role == "candidate" and q_unique:
                header_weight = sum(
                    q_weights[t] for t in q_unique if t in h_tok_set
                ) / matched_q_weight_sum
                if header_weight > 0.0:
                    header_boost = min(0.14, 0.18 * header_weight)

            raw_lexical = (
                0.40 * bm25_norm
                + 0.24 * tfidf_cos
                + path_boost
                + ident_boost
                + header_boost
            )
            lexical_score = (
                min(0.99, 1.0 - math.exp(-2.35 * raw_lexical)) if raw_lexical > 0.0 else 0.0
            )

            if role == "counterfactual":
                score = _score_counterfactual_query(request.query_text, lexical_score)
            elif role == "directive":
                cf = _score_counterfactual_query(request.query_text, 0.0)
                score = min(0.96, max(0.02, 1.0 - cf))
            elif role == "inv_secret":
                score = _score_secret_invariant_query(request.query_text, lexical_score)
            elif role == "inv_cage":
                score = _score_cage_invariant_query(request.query_text, lexical_score)
            else:
                score = lexical_score

            results[head.head_id] = float(min(1.0, max(0.0, score)))

        return results

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class _FallbackPairTokenizer:
    """Pure-Python deterministic pair tokenizer for CI without `tokenizer.json`."""

    PAD_ID = 0
    UNK_ID = 100
    CLS_ID = 101
    SEP_ID = 102
    VOCAB_OFFSET = 1000
    VOCAB_MOD = 29522

    def _tok_ids(self, text: str, max_tokens: int) -> list[int]:
        ids: list[int] = []
        for raw in _TOKEN_SPLIT_RE.findall(text):
            low = raw.lower()
            if not low:
                continue
            tid = (zlib.crc32(low.encode("utf-8")) % self.VOCAB_MOD) + self.VOCAB_OFFSET
            ids.append(tid)
            if len(ids) >= max_tokens:
                break
        if not ids:
            ids.append(self.UNK_ID)
        return ids

    def encode_batch(
        self,
        pairs: list[tuple[str, str]],
        *,
        max_length: int = 512,
    ) -> tuple[Any, Any, Any]:
        import numpy as np

        budget = max(8, max_length - 3)
        q_budget = max(4, budget // 2)
        c_budget = max(4, budget - q_budget)

        seq_ids: list[list[int]] = []
        seq_types: list[list[int]] = []
        max_len = 0

        for query, candidate in pairs:
            q_ids = self._tok_ids(query, q_budget)
            c_ids = self._tok_ids(candidate, c_budget)
            row_ids = [self.CLS_ID, *q_ids, self.SEP_ID, *c_ids, self.SEP_ID]
            if len(row_ids) > max_length:
                row_ids = row_ids[: max_length - 1] + [self.SEP_ID]
            row_types = [0] * (len(q_ids) + 2) + [1] * (len(row_ids) - len(q_ids) - 2)
            seq_ids.append(row_ids)
            seq_types.append(row_types)
            if len(row_ids) > max_len:
                max_len = len(row_ids)

        batch_size = len(pairs)
        input_ids = np.full((batch_size, max_len), self.PAD_ID, dtype=np.int64)
        attention_mask = np.zeros((batch_size, max_len), dtype=np.int64)
        token_type_ids = np.zeros((batch_size, max_len), dtype=np.int64)

        for idx, (row_ids, row_types) in enumerate(zip(seq_ids, seq_types, strict=True)):
            length = len(row_ids)
            input_ids[idx, :length] = row_ids
            attention_mask[idx, :length] = 1
            token_type_ids[idx, :length] = row_types

        return input_ids, attention_mask, token_type_ids


def load_pair_tokenizer(tokenizer: Any = None, tokenizer_path: Path | None = None) -> Any:
    """Return provided tokenizer, load `tokenizers.Tokenizer`, or use deterministic fallback."""
    if tokenizer is not None:
        return tokenizer
    if tokenizer_path is not None and tokenizer_path.is_file():
        from tokenizers import Tokenizer  # type: ignore[import-untyped]

        tok = Tokenizer.from_file(str(tokenizer_path))
        if getattr(tok, "truncation", None) is None:
            tok.enable_truncation(max_length=512)
        if getattr(tok, "padding", None) is None:
            pad_id = tok.token_to_id("[PAD]")
            if pad_id is None:
                pad_id = tok.token_to_id("<pad>")
            tok.enable_padding(pad_id=pad_id if pad_id is not None else 0)
        return tok
    return _FallbackPairTokenizer()


def tokenize_pairs_to_numpy(
    tokenizer: Any,
    query_text: str,
    candidate_texts: list[str],
    *,
    max_length: int = 512,
) -> tuple[Any, Any, Any]:
    """Batch-encode `(query_text, candidate_text)` pairs into int64 numpy tensors."""
    import numpy as np

    pairs = [(query_text, cand) for cand in candidate_texts]
    if isinstance(tokenizer, _FallbackPairTokenizer):
        return tokenizer.encode_batch(pairs, max_length=max_length)

    if hasattr(tokenizer, "encode_batch"):
        if getattr(tokenizer, "truncation", None) is None and hasattr(
            tokenizer, "enable_truncation"
        ):
            tokenizer.enable_truncation(max_length=max_length)
        if getattr(tokenizer, "padding", None) is None and hasattr(tokenizer, "enable_padding"):
            pad_id = 0
            if hasattr(tokenizer, "token_to_id"):
                resolved = tokenizer.token_to_id("[PAD]") or tokenizer.token_to_id("<pad>")
                if resolved is not None:
                    pad_id = int(resolved)
            tokenizer.enable_padding(pad_id=pad_id)

        encodings = tokenizer.encode_batch(pairs)
        raw_ids = [list(enc.ids) for enc in encodings]
        raw_masks = [
            (
                list(enc.attention_mask)
                if getattr(enc, "attention_mask", None) is not None
                else [1] * len(enc.ids)
            )
            for enc in encodings
        ]
        raw_types = [
            list(enc.type_ids) if getattr(enc, "type_ids", None) is not None else [0] * len(enc.ids)
            for enc in encodings
        ]
        max_len = max((len(row) for row in raw_ids), default=1)
        batch_size = len(pairs)
        input_ids = np.zeros((batch_size, max_len), dtype=np.int64)
        attention_mask = np.zeros((batch_size, max_len), dtype=np.int64)
        token_type_ids = np.zeros((batch_size, max_len), dtype=np.int64)
        for i in range(batch_size):
            ln = len(raw_ids[i])
            input_ids[i, :ln] = raw_ids[i]
            attention_mask[i, :ln] = raw_masks[i]
            token_type_ids[i, :ln] = raw_types[i]
        return input_ids, attention_mask, token_type_ids

    if callable(tokenizer):
        encoded = tokenizer(
            [query_text] * len(candidate_texts),
            candidate_texts,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="np",
        )
        input_ids = np.asarray(encoded["input_ids"], dtype=np.int64)
        attention_mask = np.asarray(
            encoded.get("attention_mask", np.ones_like(input_ids)),
            dtype=np.int64,
        )
        token_type_ids = np.asarray(
            encoded.get("token_type_ids", np.zeros_like(input_ids)),
            dtype=np.int64,
        )
        return input_ids, attention_mask, token_type_ids

    raise TypeError(f"Unsupported tokenizer object: {type(tokenizer).__name__}")


def _execution_providers_for_device(device: str) -> list[str]:
    normalized = device.strip().lower()
    if normalized in {"cuda", "gpu"}:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if normalized == "dml":
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    if normalized == "coreml":
        return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def _invoke_session_factory(factory: Any, model_path: Path, device: str) -> Any:
    """Instantiate or unwrap an injected ONNX InferenceSession cleanly."""
    if hasattr(factory, "run"):
        return factory
    if not callable(factory):
        raise TypeError(f"session_factory must be callable or provide .run(): {factory!r}")

    sig = inspect.signature(factory)
    pos_params = [
        p
        for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
    if has_varargs or len(pos_params) >= 2:
        return factory(str(model_path), device)
    if len(pos_params) == 1:
        return factory(str(model_path))
    return factory()


def resolve_onnx_session(
    model_path: Path,
    device: str,
    session_factory: Any = None,
) -> tuple[Any | None, str | None]:
    """Load an ONNX InferenceSession lazily (returns `(session, missing_reason)`)."""
    if session_factory is not None:
        return _invoke_session_factory(session_factory, model_path, device), None

    if not model_path.is_file():
        return None, f"ONNX model file not found at {model_path}."

    try:
        # Lazy import: `onnxruntime` is an optional `[local]` extra.
        import onnxruntime as ort  # type: ignore[import-not-found]
    except ImportError:
        return None, "Optional dependency `onnxruntime` is not installed."

    providers = _execution_providers_for_device(device)
    session = ort.InferenceSession(str(model_path), providers=providers)
    return session, None


def extract_logits_from_outputs(outputs: Any, batch_size: int) -> list[float]:
    """Extract raw cross-encoder logits from `[B]`, `[B, 1]`, or `[B, 2]` arrays."""
    import numpy as np

    if isinstance(outputs, dict):
        raw = outputs["logits"] if "logits" in outputs else next(iter(outputs.values()))
    elif isinstance(outputs, (list, tuple)):
        if len(outputs) == 0:
            raise ValueError("ONNX session returned empty outputs.")
        first = outputs[0]
        if hasattr(first, "shape"):
            # Standard ONNX Runtime output list: [logits_tensor, ...].
            raw = first
        elif (
            isinstance(first, (list, tuple))
            and len(outputs) == 1
            and first
            and isinstance(first[0], (list, tuple))
        ):
            raw = first
        else:
            raw = outputs
    else:
        raw = outputs

    logits = np.asarray(raw, dtype=np.float64)
    if logits.ndim == 1 and logits.shape[0] == batch_size:
        return [float(val) for val in logits]
    if logits.ndim == 2 and logits.shape == (batch_size, 1):
        return [float(val) for val in logits[:, 0]]
    if logits.ndim == 2 and logits.shape == (batch_size, 2):
        # Cross-encoder binary classification: positive minus negative class logit.
        return [float(val) for val in (logits[:, 1] - logits[:, 0])]
    raise ValueError(
        f"Expected ONNX logits shape ({batch_size},), ({batch_size}, 1), or ({batch_size}, 2); "
        f"got {logits.shape}."
    )


def run_onnx_cross_encoder(
    session: Any,
    tokenizer: Any,
    query_text: str,
    heads: list[ClassifierHead],
    *,
    max_length: int = 512,
) -> dict[str, float]:
    """Synchronous batched tokenization + ONNX inference (intended for `asyncio.to_thread`)."""
    candidate_texts = [head.candidate_text for head in heads]
    input_ids, attention_mask, token_type_ids = tokenize_pairs_to_numpy(
        tokenizer,
        query_text,
        candidate_texts,
        max_length=max_length,
    )

    input_names: list[str] = []
    if hasattr(session, "get_inputs"):
        input_names = [inp.name for inp in session.get_inputs()]

    if input_names:
        feed: dict[str, Any] = {}
        available = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        for name in input_names:
            if name in available:
                feed[name] = available[name]
        if not feed:
            feed[input_names[0]] = input_ids
    else:
        feed = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }

    outputs = session.run(None, feed)
    logits = extract_logits_from_outputs(outputs, len(heads))
    return {head.head_id: logits[idx] for idx, head in enumerate(heads)}


async def _atomic_download_file(url: str, dest: Path, downloader: Any = None) -> Path:
    """Download `url` to a temporary file in `dest.parent` and atomically replace `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = tempfile.NamedTemporaryFile(
        dir=dest.parent,
        prefix=f".{dest.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(tmp_file.name)
    tmp_file.close()

    try:
        if downloader is not None:
            sig = inspect.signature(downloader)
            pos_params = [
                p
                for p in sig.parameters.values()
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            has_varargs = any(
                p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
            )
            if has_varargs or len(pos_params) >= 2:
                res = downloader(url, tmp_path)
            else:
                res = downloader(url)
            if inspect.isawaitable(res):
                res = await res

            if (not tmp_path.is_file() or tmp_path.stat().st_size == 0) and res is not None:
                data = res.encode("utf-8") if isinstance(res, str) else bytes(res)
                tmp_path.write_bytes(data)
        else:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=120.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    with tmp_path.open("wb") as handle:
                        async for chunk in resp.content.iter_chunked(65536):
                            if chunk:
                                handle.write(chunk)

        if not tmp_path.is_file() or tmp_path.stat().st_size == 0:
            raise RuntimeError(f"Downloaded asset from {url!r} was empty.")

        os.replace(tmp_path, dest)
        return dest
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class LocalOnnxDriver(ClassifierDriver):
    """Quantized INT8 local ONNX cross-encoder driver."""

    def __init__(
        self,
        model_path: str = "~/.quadgate/models/modernbert-int8.onnx",
        device: str = "cpu",
        calibration_profile: CalibrationProfile | None = None,
        *,
        tokenizer_path: str | None = None,
        model_url: str | None = None,
        tokenizer_url: str | None = None,
        session_factory: Any = None,
        tokenizer: Any = None,
        fallback_to_zero_dep: bool = False,
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.tokenizer_path = tokenizer_path
        self.model_url = (
            model_url or os.environ.get("QUADGATE_ONNX_MODEL_URL") or DEFAULT_LOCAL_MODEL_URL
        )
        self.tokenizer_url = (
            tokenizer_url
            or os.environ.get("QUADGATE_ONNX_TOKENIZER_URL")
            or DEFAULT_LOCAL_TOKENIZER_URL
        )
        self._session_factory = session_factory
        self._tokenizer = tokenizer
        self.fallback_to_zero_dep = fallback_to_zero_dep
        self._session: Any | None = None
        self._zero_dep: ZeroDepDriver | None = None
        super().__init__(calibration_profile=calibration_profile)

    @property
    def driver_id(self) -> str:
        return "onnx_local"

    @property
    def capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            is_cloud=False,
            supports_parallel_heads=False,
            max_candidates_per_pass=32,
            scores_are_logits=True,
        )

    @property
    def _resolved_model_path(self) -> Path:
        return Path(self.model_path).expanduser()

    @property
    def _resolved_tokenizer_path(self) -> Path:
        if self.tokenizer_path is not None:
            return Path(self.tokenizer_path).expanduser()
        return self._resolved_model_path.parent / "tokenizer.json"

    async def ensure_model_downloaded(self, *, downloader: Any = None) -> Path:
        """Lazily download ONNX weights and `tokenizer.json` atomically."""
        model_dest = self._resolved_model_path
        tok_dest = self._resolved_tokenizer_path

        if not (model_dest.is_file() and model_dest.stat().st_size > 0):
            await _atomic_download_file(self.model_url, model_dest, downloader=downloader)
        if not (tok_dest.is_file() and tok_dest.stat().st_size > 0):
            await _atomic_download_file(self.tokenizer_url, tok_dest, downloader=downloader)
        return model_dest

    def _get_session(self) -> tuple[Any | None, str | None]:
        if self._session is not None:
            return self._session, None
        session, reason = resolve_onnx_session(
            self._resolved_model_path,
            self.device,
            session_factory=self._session_factory,
        )
        if session is not None:
            self._session = session
        return session, reason

    def _get_tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._tokenizer = load_pair_tokenizer(None, self._resolved_tokenizer_path)
        return self._tokenizer

    async def _score_via_zero_dep_logits(self, request: ClassifierRequest) -> dict[str, float]:
        if self._zero_dep is None:
            self._zero_dep = ZeroDepDriver()
        probs = await self._zero_dep.score_raw(request)
        logits: dict[str, float] = {}
        for head_id, prob in probs.items():
            clipped = min(1.0 - 1e-6, max(1e-6, float(prob)))
            logits[head_id] = math.log(clipped / (1.0 - clipped))
        return logits

    async def score_raw(self, request: ClassifierRequest) -> dict[str, float]:
        if len(request.heads) > self.capabilities.max_candidates_per_pass:
            raise ValueError(
                f"Driver {self.driver_id!r} accepts at most "
                f"{self.capabilities.max_candidates_per_pass} heads per pass; "
                f"got {len(request.heads)}."
            )

        session, missing_reason = self._get_session()
        if session is None:
            if self.fallback_to_zero_dep:
                return await self._score_via_zero_dep_logits(request)
            raise RuntimeError(
                f"LocalOnnxDriver unavailable: {missing_reason} Run `{_INSTALL_HINT}`."
            )

        tokenizer = self._get_tokenizer()
        return await asyncio.to_thread(
            run_onnx_cross_encoder,
            session,
            tokenizer,
            request.query_text,
            request.heads,
        )

    async def health_check(self) -> bool:
        try:
            session, _ = self._get_session()
            return session is not None
        except Exception:
            return False

    async def close(self) -> None:
        self._session = None
        if self._zero_dep is not None:
            await self._zero_dep.close()


__all__ = [
    "DEFAULT_LOCAL_MODEL_URL",
    "DEFAULT_LOCAL_TOKENIZER_URL",
    "LocalOnnxDriver",
    "ZeroDepDriver",
    "extract_logits_from_outputs",
    "load_pair_tokenizer",
    "resolve_onnx_session",
    "run_onnx_cross_encoder",
    "tokenize_pairs_to_numpy",
]
