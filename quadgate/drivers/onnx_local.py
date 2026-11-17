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
_PATH_OR_FILE_RE = re.compile(r"(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}")
_SNAKE_OR_CAMEL_ID_RE = re.compile(
    r"\b(?:[a-z]+_[a-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|[a-z]+(?:[A-Z][a-z0-9]+)+)\b"
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
    r"sensitive\s+environment|pii|ssn)\b|\.env\b",
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
    r"\bcat\s+[^\n]*\.env|\bsource\s+[^\n]*\.env)",
    re.IGNORECASE,
)
_HARDCODED_SECRET_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|"
    r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}|"
    r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
)
_AIRGAP_TAG_RE = re.compile(
    r"\[(?:private|confidential|airgap|tier1-private)\]",
    re.IGNORECASE,
)
_PATH_ESCAPE_RE = re.compile(r"(?:\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows|~/\.ssh)", re.I)

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


def _stem_token(token: str) -> str:
    """Apply lightweight deterministic suffix normalization for lexical matching."""
    n = len(token)
    if n < 5:
        return token
    for suffix, repl in (
        ("ifications", "if"),
        ("ification", "if"),
        ("izations", "iz"),
        ("ization", "iz"),
        ("ations", "at"),
        ("ation", "at"),
        ("ators", "at"),
        ("ator", "at"),
        ("ments", "ment"),
        ("ness", ""),
        ("ing", ""),
        ("ies", "y"),
        ("ied", "y"),
    ):
        if token.endswith(suffix) and len(token) - len(suffix) + len(repl) >= 3:
            return token[: -len(suffix)] + repl
    if token.endswith("ed") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("es") and n - 2 >= 4 and not token.endswith(("ses", "ss")):
        return token[:-2]
    if token.endswith("er") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("or") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("ly") and n - 2 >= 4:
        return token[:-2]
    if token.endswith("s") and n - 1 >= 4 and not token.endswith("ss"):
        return token[:-1]
    if token.endswith("y") and n - 1 >= 4:
        return token[:-1] + "i"
    return token


def _extract_tokens_and_identifiers(text: str) -> tuple[list[str], set[str], set[str]]:
    """Split paths, camelCase, PascalCase, and snake_case into normalized tokens."""
    stemmed_tokens: list[str] = []
    identifiers: set[str] = set()
    stems: set[str] = set()

    for match in _PATH_OR_FILE_RE.finditer(text):
        raw_path = match.group(0).replace("\\", "/").lower()
        identifiers.add(raw_path)
        basename = raw_path.rsplit("/", 1)[-1]
        identifiers.add(basename)
        stem = basename.rsplit(".", 1)[0]
        if stem and not stem.startswith("."):
            stems.add(stem)
            identifiers.add(stem)

    for match in _SNAKE_OR_CAMEL_ID_RE.finditer(text):
        ident = match.group(0).lower()
        if len(ident) >= 3:
            identifiers.add(ident)

    for chunk in _TOKEN_SPLIT_RE.findall(text):
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
                if len(low) >= 3:
                    identifiers.add(low)

    return stemmed_tokens, identifiers, stems


def _classify_head_role(head: ClassifierHead) -> str:
    """Identify whether a head evaluates a special pragmatic/invariant signal."""
    hid = head.head_id.lower()
    head_type = str(head.metadata.get("head_type", "")).lower()
    cand = head.candidate_text
    combined_id = f"{hid} {head_type}"
    if (
        "counterfactual" in combined_id
        or "brainstorm" in combined_id
        or _COUNTERFACTUAL_HEAD_RE.search(cand)
    ):
        return "counterfactual"
    if _DIRECTIVE_HEAD_RE.search(cand):
        return "directive"
    if "inv_01" in combined_id or "secret" in combined_id or _SECRET_HEAD_RE.search(cand):
        return "inv_secret"
    if "inv_02" in combined_id or "cage" in combined_id or _CAGE_HEAD_RE.search(cand):
        return "inv_cage"
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
        score += 0.72
    return min(0.97, max(0.02, score))


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

        q_tokens, q_idents, q_stems = _extract_tokens_and_identifiers(request.query_text)
        q_counts = Counter(q_tokens)
        q_unique = list(q_counts.keys())

        parsed_heads: list[tuple[ClassifierHead, list[str], set[str], set[str]]] = []
        df: Counter[str] = Counter()
        total_dl = 0
        for head in request.heads:
            c_tokens, c_idents, c_stems = _extract_tokens_and_identifiers(head.candidate_text)
            parsed_heads.append((head, c_tokens, c_idents, c_stems))
            total_dl += len(c_tokens)
            for tok in set(c_tokens):
                df[tok] += 1

        n_docs = len(parsed_heads)
        avg_dl = max(1.0, total_dl / max(1, n_docs))

        # Smoothed positive IDF ensures valid weighting even when n_docs == 1.
        def _idf(tok: str) -> float:
            return math.log(1.0 + (n_docs + 1.0) / (df.get(tok, 0) + 0.5))

        q_weights: dict[str, float] = {
            tok: _idf(tok) * (1.0 + 0.25 * math.log1p(q_counts[tok] - 1))
            for tok in q_unique
        }
        q_norm_l2 = math.sqrt(sum(w * w for w in q_weights.values())) or 1.0
        q_weight_sum = sum(q_weights.values()) or 1.0

        k1 = 1.2
        b = 0.65
        results: dict[str, float] = {}

        for head, c_tokens, c_idents, c_stems in parsed_heads:
            c_counts = Counter(c_tokens)
            dl = len(c_tokens)
            dl_norm = 1.0 - b + b * (dl / avg_dl)

            bm25_acc = 0.0
            dot_product = 0.0
            if dl > 0 and q_unique:
                for tok in q_unique:
                    tf = c_counts.get(tok, 0)
                    if tf == 0:
                        continue
                    qw = q_weights[tok]
                    bm25_term = (tf * (k1 + 1.0)) / (tf + k1 * dl_norm)
                    bm25_acc += qw * min(1.15, bm25_term)
                    c_w = _idf(tok) * (1.0 + math.log1p(tf - 1))
                    dot_product += qw * c_w

                c_norm_l2 = math.sqrt(
                    sum((_idf(t) * (1.0 + math.log1p(cnt - 1))) ** 2 for t, cnt in c_counts.items())
                )
                tfidf_cos = dot_product / (q_norm_l2 * c_norm_l2) if c_norm_l2 > 0.0 else 0.0
                bm25_norm = min(1.0, bm25_acc / q_weight_sum)
            else:
                tfidf_cos = 0.0
                bm25_norm = 0.0

            exact_ident_hits = len(q_idents & c_idents)
            exact_stem_hits = len(q_stems & c_stems)
            stem_subtoken_hits = sum(
                1 for stem in c_stems if any(qi in stem or stem in qi for qi in q_idents if len(qi) >= 3)
            )
            exact_boost = min(
                0.35,
                0.14 * exact_stem_hits + 0.07 * exact_ident_hits + 0.06 * stem_subtoken_hits,
            )

            raw_lexical = 0.55 * bm25_norm + 0.30 * tfidf_cos + exact_boost
            lexical_score = (
                min(0.99, 1.0 - math.exp(-2.6 * raw_lexical)) if raw_lexical > 0.0 else 0.0
            )

            role = _classify_head_role(head)
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
        if getattr(tokenizer, "truncation", None) is None and hasattr(tokenizer, "enable_truncation"):
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
            list(enc.attention_mask) if getattr(enc, "attention_mask", None) is not None else [1] * len(enc.ids)
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
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
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
        self.model_url = model_url or os.environ.get("QUADGATE_ONNX_MODEL_URL") or DEFAULT_LOCAL_MODEL_URL
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
        """Lazily download ONNX weights and `tokenizer.json` atomically (`quadgate init --local`)."""
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
