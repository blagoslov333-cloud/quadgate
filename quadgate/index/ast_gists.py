"""Zero-token tree-sitter gist & import fan-in builder."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import posixpath
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from quadgate.contracts import CartridgeSpec, IndexedGist
from quadgate.index.sqlite_fts import (
    check_text_air_gap,
    is_sensitive_content,
    is_sensitive_file,
    is_sensitive_path,
)

_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "target",
        ".tox",
        ".eggs",
        ".hg",
        ".svn",
    }
)

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
}

_SUPPORTED_EXTS: frozenset[str] = frozenset(_EXT_TO_LANG.keys())
_TS_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
_TS_INDEX_EXTENSIONS: tuple[str, ...] = (
    "/index.ts",
    "/index.tsx",
    "/index.js",
    "/index.jsx",
)
_SAFE_DOTENV_NAMES: frozenset[str] = frozenset(
    {".env.example", ".env.sample", ".env.template"}
)
_PRIVATE_TAGS: tuple[str, ...] = (
    "[private]",
    "[confidential]",
    "[airgap]",
    "[tier1-private]",
)

# Module-level parser cache: created once per language, never re-created per file.
_PARSER_CACHE: dict[str, Any] = {}
_THREAD_LOCAL_PARSERS = threading.local()

# Pre-compiled fallback & Markdown extraction regexes.
_RE_WS = re.compile(r"\s+")

_RE_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([^\n#;]+)", re.MULTILINE)
_RE_PY_FROM = re.compile(
    r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+([^\n#]+)", re.MULTILINE
)
_RE_PY_CLASS = re.compile(r"^[ \t]*class[ \t]+([A-Za-z_]\w*)", re.MULTILINE)
_RE_PY_DEF = re.compile(
    r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)[ \t]*(\([^)]*\))(?:[ \t]*->[ \t]*([^\n:#]+))?",
    re.MULTILINE,
)

_RE_TS_IMPORT_FROM = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:[^;'"]*?\s+from\s+)?['"]([^'"]+)['"]"""
)
_RE_TS_REQUIRE = re.compile(r"""\b(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_RE_TS_TYPE_DECL = re.compile(
    r"^[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?(?:abstract[ \t]+)?"
    r"(?:class|interface|type|enum)[ \t]+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_RE_TS_FUNC = re.compile(
    r"^[ \t]*(?:export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?(?:async[ \t]+)?"
    r"function\*?[ \t]+([A-Za-z_$][\w$]*)[ \t]*(?:<[^>\n]*>)?[ \t]*(\([^)]*\))"
    r"(?:[ \t]*:[ \t]*([^\{\n;]+))?",
    re.MULTILINE,
)
_RE_TS_ARROW = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)"
    r"(?:[ \t]*:[^=\n]+)?[ \t]*=[ \t]*(?:async[ \t]*)?(?:<[^>\n]*>)?[ \t]*"
    r"(\([^)]*\))(?:[ \t]*:[ \t]*([^=\n]+))?[ \t]*=>",
    re.MULTILINE,
)
_RE_TS_METHOD = re.compile(
    r"^[ \t]{2,}(?:public[ \t]+|private[ \t]+|protected[ \t]+|"
    r"static[ \t]+|readonly[ \t]+|async[ \t]+)*"
    r"([A-Za-z_$][\w$]*)[ \t]*(\([^)]*\))(?:[ \t]*:[ \t]*([^\{\n;]+))?[ \t]*\{",
    re.MULTILINE,
)
_TS_RESERVED_WORDS: frozenset[str] = frozenset(
    {"if", "for", "while", "switch", "catch", "constructor", "return", "else"}
)

_RE_RUST_USE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?use[ \t]+([^;]+);", re.MULTILINE
)
_RE_RUST_MOD = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?mod[ \t]+([A-Za-z_]\w*)[ \t]*;", re.MULTILINE
)
_RE_RUST_TYPE = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:struct|enum|trait|type)[ \t]+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_RE_RUST_FN = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?(?:unsafe[ \t]+)?(?:const[ \t]+)?"
    r"(?:extern[ \t]+\"[^\"]+\"[ \t]+)?fn[ \t]+([A-Za-z_]\w*)[ \t]*(?:<[^>\n]*>)?[ \t]*"
    r"(\([^)]*\))(?:[ \t]*->[ \t]*([^\{\n;]+))?",
    re.MULTILINE,
)

_RE_GO_MOD = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_RE_GO_IMPORT_SINGLE = re.compile(
    r"""^[ \t]*import[ \t]+(?:[A-Za-z_.\w]*[ \t]+)?"([^"]+)" """, re.MULTILINE | re.VERBOSE
)
_RE_GO_IMPORT_BLOCK = re.compile(r"^[ \t]*import[ \t]*\(([^)]*)\)", re.MULTILINE | re.DOTALL)
_RE_GO_QUOTED = re.compile(r'"([^"]+)"')
_RE_GO_TYPE = re.compile(r"^[ \t]*type[ \t]+([A-Za-z_]\w*)\b", re.MULTILINE)
_RE_GO_FUNC = re.compile(
    r"^[ \t]*func[ \t]+(?:\([^)]+\)[ \t]+)?([A-Za-z_]\w*)[ \t]*(\([^)]*\))"
    r"(?:[ \t]*([^\{\n]+))?",
    re.MULTILINE,
)

_RE_MD_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_RE_MD_ATX = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_RE_MD_SETEXT = re.compile(r"^([^\n]+)\n[ \t]*([=-]{2,})[ \t]*$", re.MULTILINE)
_RE_MD_LINK = re.compile(r"""(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+"[^"]*")?\)""")
_RE_MD_WIKI = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


@dataclass(slots=True)
class _ParsedRecord:
    rel_path: str
    content_hash: str
    language: str
    semantic_gist: str
    symbols: list[str]
    raw_imports: list[tuple[str, list[str]]]
    mtime_ns: int
    is_private_tier1: bool


def _collapse_ws(text: str, limit: int = 320) -> str:
    if "\n" not in text and "\r" not in text and "\t" not in text and "  " not in text:
        collapsed = text.strip()
    else:
        collapsed = _RE_WS.sub(" ", text).strip()
    if len(collapsed) > limit:
        return collapsed[: limit - 3].rstrip() + "..."
    return collapsed


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a workspace glob pattern to a POSIX path regex (handling **/* and tags)."""
    norm = pattern.replace("\\", "/").lstrip("./")
    parts: list[str] = ["^"]
    i = 0
    n = len(norm)
    while i < n:
        if norm[i : i + 3] == "**/":
            parts.append("(?:.+/)?")
            i += 3
        elif norm[i : i + 3] == "/**":
            parts.append("(?:/.*)?")
            i += 3
        elif norm[i : i + 2] == "**":
            parts.append(".*")
            i += 2
        elif norm[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif norm[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(norm[i]))
            i += 1
    parts.append("$")
    return re.compile("".join(parts))


def _get_ts_parser(language: str) -> Any | None:
    """Return a cached tree-sitter Parser for `language`, or None if unavailable."""
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    try:
        import tree_sitter_language_pack as tslp
    except ImportError:
        return None
    if tslp is None or not hasattr(tslp, "get_parser"):
        return None
    try:
        parser = tslp.get_parser(cast(Any, language))
    except Exception:
        return None
    _PARSER_CACHE[language] = parser
    return parser


def _get_thread_ts_parser(language: str) -> Any | None:
    """Return a thread-safe cached tree-sitter Parser for `language`."""
    base_parser = _get_ts_parser(language)
    if base_parser is None or threading.current_thread() is threading.main_thread():
        return base_parser
    cache: dict[tuple[str, int], Any] | None = getattr(_THREAD_LOCAL_PARSERS, "parsers", None)
    if cache is None:
        cache = {}
        _THREAD_LOCAL_PARSERS.parsers = cache
    key = (language, id(base_parser))
    local_parser = cache.get(key)
    if local_parser is not None:
        return local_parser
    lang_obj = getattr(base_parser, "language", None)
    if lang_obj is None:
        return base_parser
    try:
        import tree_sitter

        local_parser = tree_sitter.Parser(lang_obj)
    except Exception:
        return base_parser
    cache[key] = local_parser
    return local_parser


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    for q in ('"""', "'''", '"', "'", "`"):
        if len(s) >= 2 * len(q) and s.startswith(q) and s.endswith(q):
            return s[len(q) : -len(q)]
    return s


def _dedup_preserve(items: list[str]) -> list[str]:
    if len(items) <= 1:
        return [x for x in items if x]
    return list(dict.fromkeys(x for x in items if x))


def _extract_leading_comments(text: str, prefixes: tuple[str, ...]) -> str:
    lines: list[str] = []
    in_block = False
    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s:
            if lines:
                break
            continue
        if in_block:
            if "*/" in s:
                cleaned = s.split("*/", 1)[0].lstrip("* ").strip()
                if cleaned:
                    lines.append(cleaned)
                break
            cleaned = s.lstrip("* ").strip()
            if cleaned:
                lines.append(cleaned)
            continue
        if s.startswith(("/**", "/*")):
            rest = s[3:] if s.startswith("/**") else s[2:]
            if "*/" in rest:
                cleaned = rest.split("*/", 1)[0].strip()
                if cleaned:
                    lines.append(cleaned)
                break
            cleaned = rest.lstrip("* ").strip()
            if cleaned:
                lines.append(cleaned)
            in_block = True
            continue
        matched = False
        for p in prefixes:
            if s.startswith(p):
                cleaned = s[len(p) :].strip()
                if cleaned:
                    lines.append(cleaned)
                matched = True
                break
        if not matched:
            break
    return _collapse_ws(" ".join(lines)) if lines else ""


def _expand_rust_use_tree(expr: str) -> list[str]:
    """Expand `crate::a::{b, c::d}` into flat Rust use paths."""
    cleaned = _RE_WS.sub(" ", expr.replace("\n", " ")).strip().rstrip(";")
    if "{" not in cleaned:
        path = cleaned.split(" as ", 1)[0].strip()
        return [path] if path and path != "*" else []

    results: list[str] = []

    def _walk(prefix: str, body: str) -> None:
        items: list[str] = []
        depth = 0
        start = 0
        for idx, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                items.append(body[start:idx].strip())
                start = idx + 1
        tail = body[start:].strip()
        if tail:
            items.append(tail)
        for item in items:
            if not item or item == "*":
                continue
            if "{" in item:
                sub_prefix, sub_rest = item.split("{", 1)
                sub_prefix = sub_prefix.strip().rstrip(":")
                sub_body = sub_rest.rsplit("}", 1)[0]
                joined = (
                    f"{prefix}::{sub_prefix}"
                    if prefix and sub_prefix
                    else (prefix or sub_prefix)
                )
                _walk(joined, sub_body)
            else:
                leaf = item.split(" as ", 1)[0].strip()
                if leaf == "self":
                    if prefix:
                        results.append(prefix)
                elif leaf and leaf != "*":
                    results.append(f"{prefix}::{leaf}" if prefix else leaf)

    head, rest = cleaned.split("{", 1)
    head = head.strip().rstrip(":")
    inner = rest.rsplit("}", 1)[0]
    _walk(head, inner)
    return results


def _normalize_md_link(raw_target: str) -> str | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    lower = target.lower()
    if lower.startswith(("http://", "https://", "mailto:", "tel:", "data:", "javascript:")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    return target or None


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Tree-sitter per-language extractors
# ---------------------------------------------------------------------------


def _ts_extract_python(
    root: Any, data: bytes
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    docstring = ""
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []

    def _collect_func(fn_node: Any) -> None:
        name_node = fn_node.child_by_field_name("name")
        if name_node is None:
            return
        name = data[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")
        if name.startswith("_"):
            return
        for ch in reversed(fn_node.children):
            if ch.type == ":":
                sig = _collapse_ws(
                    data[name_node.start_byte : ch.start_byte].decode("utf-8", "replace"),
                    200,
                )
                if sig:
                    symbols.append(sig)
                    return
                break
        params_node = fn_node.child_by_field_name("parameters")
        params = (
            _collapse_ws(
                data[params_node.start_byte : params_node.end_byte].decode("utf-8", "replace"),
                120,
            )
            if params_node is not None
            else "()"
        )
        ret_node = fn_node.child_by_field_name("return_type")
        ret = (
            " -> "
            + _collapse_ws(
                data[ret_node.start_byte : ret_node.end_byte].decode("utf-8", "replace"),
                60,
            )
            if ret_node is not None
            else ""
        )
        symbols.append(f"{name}{params}{ret}")

    for child in root.children:
        ctype = child.type
        if ctype == "expression_statement" and not docstring:
            ch0 = child.children[0] if child.children else None
            if ch0 is not None and ch0.type == "string":
                docstring = _collapse_ws(
                    _strip_quotes(data[ch0.start_byte : ch0.end_byte].decode("utf-8", "replace"))
                )
                continue
        elif ctype == "string" and not docstring:
            docstring = _collapse_ws(
                _strip_quotes(data[child.start_byte : child.end_byte].decode("utf-8", "replace"))
            )
            continue

        if ctype == "decorated_definition":
            defn = child.child_by_field_name("definition")
            if defn is None:
                for sub in child.children:
                    if sub.type in ("class_definition", "function_definition"):
                        defn = sub
                        break
            if defn is None:
                continue
            child = defn
            ctype = defn.type

        if ctype == "import_statement":
            for sub in child.children:
                if sub.type == "dotted_name":
                    imports.append(
                        (data[sub.start_byte : sub.end_byte].decode("utf-8", "replace"), [])
                    )
                elif sub.type == "aliased_import":
                    nm = sub.child_by_field_name("name")
                    if nm is not None:
                        imports.append(
                            (data[nm.start_byte : nm.end_byte].decode("utf-8", "replace"), [])
                        )
        elif ctype == "import_from_statement":
            mod_node = child.child_by_field_name("module_name")
            mod_text = (
                data[mod_node.start_byte : mod_node.end_byte].decode("utf-8", "replace").strip()
                if mod_node is not None
                else ""
            )
            imported_names: list[str] = []
            seen_import_kw = False
            for sub in child.children:
                st = sub.type
                if st == "import":
                    seen_import_kw = True
                elif seen_import_kw:
                    if st == "dotted_name":
                        imported_names.append(
                            data[sub.start_byte : sub.end_byte].decode("utf-8", "replace").strip()
                        )
                    elif st == "aliased_import":
                        nm = sub.child_by_field_name("name")
                        if nm is not None:
                            imported_names.append(
                                data[nm.start_byte : nm.end_byte].decode("utf-8", "replace").strip()
                            )
            if mod_text.endswith(".") and imported_names:
                for name in imported_names:
                    imports.append((f"{mod_text}{name}", []))
            elif mod_text:
                imports.append((mod_text, imported_names))
        elif ctype == "class_definition":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                cname = data[name_node.start_byte : name_node.end_byte].decode("utf-8", "replace")
                if not cname.startswith("_"):
                    symbols.append(cname)
                body = child.child_by_field_name("body")
                if body is not None:
                    for member in body.children:
                        mtype = member.type
                        if mtype == "expression_statement" and not docstring:
                            m0 = member.children[0] if member.children else None
                            if m0 is not None and m0.type == "string":
                                docstring = _collapse_ws(
                                    _strip_quotes(
                                        data[m0.start_byte : m0.end_byte].decode("utf-8", "replace")
                                    )
                                )
                                continue
                        elif mtype == "string" and not docstring:
                            docstring = _collapse_ws(
                                _strip_quotes(
                                    data[member.start_byte : member.end_byte].decode(
                                        "utf-8", "replace"
                                    )
                                )
                            )
                            continue
                        if mtype == "decorated_definition":
                            defn = member.child_by_field_name("definition")
                            if defn is not None and defn.type == "function_definition":
                                _collect_func(defn)
                        elif mtype == "function_definition":
                            _collect_func(member)
        elif ctype == "function_definition":
            _collect_func(child)
            if not docstring:
                body = child.child_by_field_name("body")
                if body is not None and body.children:
                    first = body.children[0]
                    if first.type == "expression_statement" and first.children:
                        f0 = first.children[0]
                        if f0.type == "string":
                            docstring = _collapse_ws(
                                _strip_quotes(
                                    data[f0.start_byte : f0.end_byte].decode("utf-8", "replace")
                                )
                            )
                    elif first.type == "string":
                        docstring = _collapse_ws(
                            _strip_quotes(
                                data[first.start_byte : first.end_byte].decode("utf-8", "replace")
                            )
                        )

    return docstring, _dedup_preserve(symbols), imports


def _ts_extract_js_ts(
    root: Any, data: bytes, text: str
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//",))
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []

    def _nt(n: Any) -> str:
        return data[n.start_byte : n.end_byte].decode("utf-8", "replace")

    def _fn_sig(fn_node: Any, name: str) -> str:
        params_node = fn_node.child_by_field_name("parameters")
        params = _collapse_ws(_nt(params_node), 120) if params_node is not None else "()"
        ret_node = fn_node.child_by_field_name("return_type")
        ret = _collapse_ws(_nt(ret_node), 60) if ret_node is not None else ""
        if ret and not ret.startswith(":"):
            ret = f": {ret}"
        return f"{name}{params}{ret}"

    def _scan_requires(node: Any) -> None:
        stack = [node]
        while stack:
            cur = stack.pop()
            if cur.type == "call_expression":
                fn = cur.child_by_field_name("function")
                args = cur.child_by_field_name("arguments")
                if fn is not None and args is not None and _nt(fn) in ("require", "import"):
                    for arg in args.children:
                        if arg.type == "string":
                            spec = _strip_quotes(_nt(arg))
                            if spec:
                                imports.append((spec, []))
            for ch in cur.children:
                stack.append(ch)

    def _handle_decl(node: Any) -> None:
        ntype = node.type
        if ntype in ("class_declaration", "abstract_class_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                symbols.append(_nt(name_node))
            body = node.child_by_field_name("body")
            if body is not None:
                for mem in body.children:
                    if mem.type == "method_definition":
                        mn = mem.child_by_field_name("name")
                        if mn is not None:
                            mname = _nt(mn)
                            if mname != "constructor" and not mname.startswith("#"):
                                symbols.append(_fn_sig(mem, mname))
        elif ntype in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                symbols.append(_nt(name_node))
        elif ntype in ("function_declaration", "generator_function_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                symbols.append(_fn_sig(node, _nt(name_node)))
        elif ntype in ("lexical_declaration", "variable_declaration"):
            for sub in node.children:
                if sub.type == "variable_declarator":
                    nm = sub.child_by_field_name("name")
                    val = sub.child_by_field_name("value")
                    if nm is not None and val is not None:
                        if val.type in ("arrow_function", "function_expression", "function"):
                            symbols.append(_fn_sig(val, _nt(nm)))
                        elif val.type == "call_expression":
                            _scan_requires(val)

    has_dyn = "require(" in text or "import(" in text
    for child in root.children:
        ctype = child.type
        if ctype == "import_statement":
            src = child.child_by_field_name("source")
            if src is not None:
                spec = _strip_quotes(_nt(src))
                if spec:
                    imports.append((spec, []))
        elif ctype == "export_statement":
            src = child.child_by_field_name("source")
            if src is not None:
                spec = _strip_quotes(_nt(src))
                if spec:
                    imports.append((spec, []))
            decl = child.child_by_field_name("declaration")
            if decl is not None:
                _handle_decl(decl)
            else:
                for sub in child.children:
                    if sub.type in (
                        "class_declaration",
                        "interface_declaration",
                        "type_alias_declaration",
                        "enum_declaration",
                        "function_declaration",
                        "lexical_declaration",
                    ):
                        _handle_decl(sub)
        else:
            _handle_decl(child)
            if has_dyn and ctype not in (
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
            ):
                _scan_requires(child)

    return gist, _dedup_preserve(symbols), imports


def _ts_extract_rust(
    root: Any, data: bytes, text: str
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//!", "///", "//"))
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []
    doc_lines: list[str] = []

    def _nt(n: Any) -> str:
        return data[n.start_byte : n.end_byte].decode("utf-8", "replace")

    def _fn_sig(fn_node: Any) -> None:
        nm = fn_node.child_by_field_name("name")
        if nm is None:
            return
        params_node = fn_node.child_by_field_name("parameters")
        params = _collapse_ws(_nt(params_node), 120) if params_node is not None else "()"
        ret_node = fn_node.child_by_field_name("return_type")
        ret = f" -> {_collapse_ws(_nt(ret_node), 60)}" if ret_node is not None else ""
        symbols.append(f"{_nt(nm)}{params}{ret}")

    for child in root.children:
        ctype = child.type
        if ctype == "line_comment" and not gist:
            raw = _nt(child).strip()
            if raw.startswith(("///", "//!")):
                cleaned = raw[3:].strip()
                if cleaned:
                    doc_lines.append(cleaned)
        elif ctype == "use_declaration":
            arg = child.child_by_field_name("argument")
            if arg is not None:
                for path in _expand_rust_use_tree(_nt(arg)):
                    imports.append((path, []))
        elif ctype == "mod_item":
            nm = child.child_by_field_name("name")
            body = child.child_by_field_name("body")
            if nm is not None and body is None:
                imports.append((f"mod::{_nt(nm)}", []))
        elif ctype in ("struct_item", "enum_item", "trait_item", "type_item"):
            nm = child.child_by_field_name("name")
            if nm is not None:
                symbols.append(_nt(nm))
        elif ctype in ("function_item", "function_signature_item"):
            _fn_sig(child)
        elif ctype == "impl_item":
            body = child.child_by_field_name("body")
            if body is not None:
                for mem in body.children:
                    if mem.type in ("function_item", "function_signature_item"):
                        _fn_sig(mem)

    if not gist and doc_lines:
        gist = _collapse_ws(" ".join(doc_lines[:4]))
    return gist, _dedup_preserve(symbols), imports


def _ts_extract_go(
    root: Any, data: bytes, text: str
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//",))
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []

    def _nt(n: Any) -> str:
        return data[n.start_byte : n.end_byte].decode("utf-8", "replace")

    def _collect_import_specs(node: Any) -> None:
        for ch in node.children:
            if ch.type == "import_spec":
                p = ch.child_by_field_name("path")
                if p is not None:
                    spec = _strip_quotes(_nt(p))
                    if spec:
                        imports.append((spec, []))
            elif ch.type == "import_spec_list":
                _collect_import_specs(ch)

    for child in root.children:
        ctype = child.type
        if ctype == "import_declaration":
            _collect_import_specs(child)
        elif ctype == "type_declaration":
            for sub in child.children:
                if sub.type == "type_spec":
                    nm = sub.child_by_field_name("name")
                    if nm is not None:
                        symbols.append(_nt(nm))
        elif ctype in ("function_declaration", "method_declaration"):
            nm = child.child_by_field_name("name")
            if nm is None:
                continue
            params_node = child.child_by_field_name("parameters")
            params = _collapse_ws(_nt(params_node), 120) if params_node is not None else "()"
            res_node = child.child_by_field_name("result")
            res = f" {_collapse_ws(_nt(res_node), 60)}" if res_node is not None else ""
            symbols.append(f"{_nt(nm)}{params}{res}")

    return gist, _dedup_preserve(symbols), imports


def _ts_extract_markdown(
    root: Any, data: bytes, text: str
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    headings: list[str] = []
    imports: list[tuple[str, list[str]]] = []

    stack = [root]
    while stack:
        cur = stack.pop()
        ctype = cur.type
        if ctype in ("atx_heading", "setext_heading"):
            hc = cur.child_by_field_name("heading_content")
            if hc is not None:
                raw = data[hc.start_byte : hc.end_byte].decode("utf-8", "replace")
            else:
                raw = data[cur.start_byte : cur.end_byte].decode("utf-8", "replace").lstrip("#")
            cleaned = _collapse_ws(raw, 120)
            if cleaned:
                headings.append(cleaned)
            continue
        if ctype == "inline":
            line_txt = data[cur.start_byte : cur.end_byte].decode("utf-8", "replace")
            if "](" in line_txt:
                for m in _RE_MD_LINK.finditer(line_txt):
                    norm = _normalize_md_link(m.group(1))
                    if norm:
                        imports.append((norm, []))
            if "[[" in line_txt:
                for m in _RE_MD_WIKI.finditer(line_txt):
                    wiki = m.group(1).strip()
                    if wiki:
                        imports.append((f"[[{wiki}]]", []))
            continue
        if ctype in ("fenced_code_block", "indented_code_block"):
            continue
        for ch in reversed(cur.children):
            stack.append(ch)

    gist = _collapse_ws(" | ".join(headings)) if headings else _collapse_ws(text, 200)
    return gist, _dedup_preserve(headings), imports


# ---------------------------------------------------------------------------
# Stdlib AST & Regex Fallbacks
# ---------------------------------------------------------------------------


def _fallback_extract_python_regex(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []
    for m in _RE_PY_IMPORT.finditer(text):
        for part in m.group(1).split(","):
            mod = part.strip().split(" as ", 1)[0].strip()
            if mod:
                imports.append((mod, []))
    for m in _RE_PY_FROM.finditer(text):
        mod = m.group(1).strip()
        raw_names = [
            p.strip().split(" as ", 1)[0].strip()
            for p in m.group(2).strip("()").split(",")
            if p.strip() and p.strip() != "*"
        ]
        if mod.endswith(".") and raw_names:
            for nm in raw_names:
                imports.append((f"{mod}{nm}", []))
        elif mod:
            imports.append((mod, raw_names))
    for m in _RE_PY_CLASS.finditer(text):
        name = m.group(1)
        if not name.startswith("_"):
            symbols.append(name)
    for m in _RE_PY_DEF.finditer(text):
        name = m.group(1)
        if name.startswith("_"):
            continue
        params = _collapse_ws(m.group(2), 120)
        ret = f" -> {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{name}{params}{ret}")
    gist = _extract_leading_comments(text, ("#",))
    return gist, _dedup_preserve(symbols), imports


def _ast_expr_str(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return ast.unparse(node)


def _format_py_ast_sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = fn.args
    parts: list[str] = []
    pos_all = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(pos_all) - len(args.defaults)) + list(args.defaults)
    for idx, (arg, default) in enumerate(zip(pos_all, defaults)):
        item = arg.arg
        if arg.annotation is not None:
            item += f": {_ast_expr_str(arg.annotation)}"
        if default is not None:
            item += f" = {_ast_expr_str(default)}"
        parts.append(item)
        if args.posonlyargs and idx == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        v = f"*{args.vararg.arg}"
        if args.vararg.annotation is not None:
            v += f": {_ast_expr_str(args.vararg.annotation)}"
        parts.append(v)
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        item = arg.arg
        if arg.annotation is not None:
            item += f": {_ast_expr_str(arg.annotation)}"
        if default is not None:
            item += f" = {_ast_expr_str(default)}"
        parts.append(item)
    if args.kwarg is not None:
        k = f"**{args.kwarg.arg}"
        if args.kwarg.annotation is not None:
            k += f": {_ast_expr_str(args.kwarg.annotation)}"
        parts.append(k)
    ret = f" -> {_ast_expr_str(fn.returns)}" if fn.returns is not None else ""
    return f"{fn.name}({', '.join(parts)}){ret}"


def _fallback_extract_python(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _fallback_extract_python_regex(text)

    docstring = _collapse_ws(ast.get_docstring(tree) or "")
    symbols: list[str] = []
    imports: list[tuple[str, list[str]]] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            names = [alias.name for alias in node.names if alias.name != "*"]
            if node.module is None and names:
                for nm in names:
                    imports.append((f"{prefix}{nm}", []))
            elif prefix:
                imports.append((prefix, names))
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                symbols.append(node.name)
            if not docstring:
                docstring = _collapse_ws(ast.get_docstring(node) or "")
            for mem in node.body:
                if isinstance(mem, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not mem.name.startswith("_"):
                        symbols.append(_format_py_ast_sig(mem))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(_format_py_ast_sig(node))
            if not docstring:
                docstring = _collapse_ws(ast.get_docstring(node) or "")

    return docstring, _dedup_preserve(symbols), imports


def _fallback_extract_js_ts(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//",))
    imports: list[tuple[str, list[str]]] = []
    for m in _RE_TS_IMPORT_FROM.finditer(text):
        imports.append((m.group(1), []))
    for m in _RE_TS_REQUIRE.finditer(text):
        imports.append((m.group(1), []))

    symbols: list[str] = []
    for m in _RE_TS_TYPE_DECL.finditer(text):
        symbols.append(m.group(1))
    for m in _RE_TS_FUNC.finditer(text):
        ret = f": {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{m.group(1)}{_collapse_ws(m.group(2), 120)}{ret}")
    for m in _RE_TS_ARROW.finditer(text):
        ret = f": {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{m.group(1)}{_collapse_ws(m.group(2), 120)}{ret}")
    for m in _RE_TS_METHOD.finditer(text):
        name = m.group(1)
        if name in _TS_RESERVED_WORDS or name.startswith("#"):
            continue
        ret = f": {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{name}{_collapse_ws(m.group(2), 120)}{ret}")
    return gist, _dedup_preserve(symbols), imports


def _fallback_extract_rust(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//!", "///", "//"))
    imports: list[tuple[str, list[str]]] = []
    for m in _RE_RUST_USE.finditer(text):
        for path in _expand_rust_use_tree(m.group(1)):
            imports.append((path, []))
    for m in _RE_RUST_MOD.finditer(text):
        imports.append((f"mod::{m.group(1)}", []))

    symbols: list[str] = []
    for m in _RE_RUST_TYPE.finditer(text):
        symbols.append(m.group(1))
    for m in _RE_RUST_FN.finditer(text):
        ret = f" -> {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{m.group(1)}{_collapse_ws(m.group(2), 120)}{ret}")
    return gist, _dedup_preserve(symbols), imports


def _fallback_extract_go(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    gist = _extract_leading_comments(text, ("//",))
    imports: list[tuple[str, list[str]]] = []
    for m in _RE_GO_IMPORT_SINGLE.finditer(text):
        imports.append((m.group(1), []))
    for m in _RE_GO_IMPORT_BLOCK.finditer(text):
        for q in _RE_GO_QUOTED.finditer(m.group(1)):
            imports.append((q.group(1), []))

    symbols: list[str] = []
    for m in _RE_GO_TYPE.finditer(text):
        symbols.append(m.group(1))
    for m in _RE_GO_FUNC.finditer(text):
        res = f" {_collapse_ws(m.group(3), 60)}" if m.group(3) else ""
        symbols.append(f"{m.group(1)}{_collapse_ws(m.group(2), 120)}{res}")
    return gist, _dedup_preserve(symbols), imports


def _fallback_extract_markdown(
    text: str,
) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    stripped = _RE_MD_FENCE.sub("", text)
    headings: list[str] = []
    for m in _RE_MD_ATX.finditer(stripped):
        h = _collapse_ws(m.group(1), 120)
        if h:
            headings.append(h)
    for m in _RE_MD_SETEXT.finditer(stripped):
        h = _collapse_ws(m.group(1), 120)
        if h and not h.startswith("#"):
            headings.append(h)

    imports: list[tuple[str, list[str]]] = []
    if "](" in stripped:
        for m in _RE_MD_LINK.finditer(stripped):
            norm = _normalize_md_link(m.group(1))
            if norm:
                imports.append((norm, []))
    if "[[" in stripped:
        for m in _RE_MD_WIKI.finditer(stripped):
            wiki = m.group(1).strip()
            if wiki:
                imports.append((f"[[{wiki}]]", []))

    gist = _collapse_ws(" | ".join(headings)) if headings else _collapse_ws(stripped, 200)
    return gist, _dedup_preserve(headings), imports


# ---------------------------------------------------------------------------
# Main Indexer Class
# ---------------------------------------------------------------------------


class AstGistIndexer:
    """Extracts structural AST gists and import fan-in blast radius at $0 LLM cost."""

    def __init__(
        self,
        workspace_root: Path,
        cartridge: CartridgeSpec | None = None,
        *,
        use_tree_sitter: bool = True,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.cartridge = cartridge
        self.use_tree_sitter = use_tree_sitter
        self._workspace_real = os.path.realpath(str(self.workspace_root))
        self._workspace_real_norm = os.path.normcase(self._workspace_real)
        self.workspace_hash = hashlib.blake2b(
            self._workspace_real_norm.encode("utf-8"), digest_size=16
        ).hexdigest()

        if cartridge is not None:
            cu = cartridge.context_unit
            self._include_res = [_glob_to_regex(g) for g in cu.include_globs]
            self._exclude_res = [_glob_to_regex(g) for g in cu.exclude_globs]
            self._private_res = [(g, _glob_to_regex(g)) for g in cu.private_tier1_globs]
        else:
            self._include_res = []
            self._exclude_res = []
            self._private_res = []

        # Incremental delta caches
        self._dir_mtimes: dict[str, int] = {}
        self._file_stats: dict[str, tuple[int, int]] = {}
        self._records: dict[str, _ParsedRecord] = {}
        self._gists_by_path: dict[str, IndexedGist] = {}
        self._reverse_edges: dict[str, set[str]] = {}
        self._cached_result: list[IndexedGist] | None = None
        self._go_module_name: str | None = None

    @staticmethod
    def _detect_language(rel_path: str) -> str:
        lower_name = posixpath.basename(rel_path).lower()
        if lower_name == ".env" or lower_name.startswith(".env."):
            return "dotenv"
        dot_idx = lower_name.rfind(".")
        if dot_idx == -1:
            return "text"
        return _EXT_TO_LANG.get(lower_name[dot_idx:], "text")

    def _is_private_tier1(self, rel_path: str, data: bytes | None = None) -> bool:
        lower = rel_path.lower()
        basename = posixpath.basename(lower)
        if basename == ".env" or basename.startswith(".env."):
            if basename not in _SAFE_DOTENV_NAMES:
                return True
        if basename.endswith((".pem", ".key")) or basename in (
            "id_rsa",
            "id_ed25519",
            "id_ecdsa",
            "id_dsa",
        ):
            return True
        if "/" in lower:
            parts = lower.split("/")
            for seg in parts[:-1]:
                if seg in (
                    "secrets",
                    "private_vault",
                    "confidential",
                    "privileged",
                    "attorney_client",
                    "payroll",
                    "tax_ids",
                ):
                    return True
            if "vault/private/" in lower or lower.startswith("vault/private/"):
                return True
        if any(tag in lower for tag in _PRIVATE_TAGS):
            return True
        for raw_glob, rx in self._private_res:
            if rx.match(rel_path):
                if basename in _SAFE_DOTENV_NAMES and ".env*" in raw_glob:
                    continue
                return True
        if data is not None and data and b"\x00" not in data[:4096]:
            head_text = data[:262_144].decode("utf-8", errors="ignore")
            if is_sensitive_content(head_text, include_tags=False):
                return True
        return False

    def has_private_files(self) -> bool:
        """Return True if any file in this workspace triggers Tier-1 privacy rules."""
        if any(gist.is_private_tier1 for gist in self._gists_by_path.values()):
            return True
        if not self.workspace_root.exists():
            return False
        excluded_dirs = _EXCLUDED_DIRS
        has_exclude_res = bool(self._exclude_res)
        indexed = self._gists_by_path
        stack: list[tuple[str, str]] = [(self._workspace_real, "")]
        while stack:
            abs_dir, rel_dir = stack.pop()
            prefix = f"{rel_dir}/" if rel_dir else ""
            try:
                with os.scandir(abs_dir) as it:
                    for entry in it:
                        name = entry.name
                        if entry.is_file(follow_symlinks=False):
                            rel_path = prefix + name
                            if has_exclude_res and self._is_excluded_path(rel_path, is_dir=False):
                                continue
                            if rel_path in indexed:
                                continue
                            if self._is_private_tier1(rel_path):
                                return True
                            if is_sensitive_file(entry.path, self._workspace_real):
                                return True
                        elif entry.is_dir(follow_symlinks=False):
                            if name in excluded_dirs:
                                continue
                            sub_rel = prefix + name
                            if has_exclude_res and self._is_excluded_path(sub_rel, is_dir=True):
                                continue
                            stack.append((entry.path, sub_rel))
            except OSError:
                continue
        return False

    def is_file_sensitive(self, file_path: Path | str) -> bool:
        """Return True if file_path triggers Tier-1 privacy rules by path, glob, or content."""
        rel_path = self._normalize_target_path(str(file_path))
        if self._is_private_tier1(rel_path):
            return True
        candidate = Path(file_path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return is_sensitive_file(candidate, str(self.workspace_root))

    def _is_excluded_path(self, rel_path: str, is_dir: bool = False) -> bool:
        if is_dir:
            probe = f"{rel_path}/"
            return any(rx.match(probe) or rx.match(rel_path) for rx in self._exclude_res)
        return any(rx.match(rel_path) for rx in self._exclude_res)

    def _is_included_file(self, rel_path: str, filename: str) -> bool:
        if self._exclude_res and self._is_excluded_path(rel_path, is_dir=False):
            return False
        if self._include_res:
            return any(rx.match(rel_path) for rx in self._include_res)
        dot_idx = filename.rfind(".")
        if dot_idx == -1:
            return False
        return filename[dot_idx:].lower() in _SUPPORTED_EXTS

    def _extract_content(
        self, rel_path: str, language: str, data: bytes, is_private: bool
    ) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
        if is_private and posixpath.basename(rel_path).lower().startswith(".env"):
            return "Private environment configuration (air-gapped)", [], []
        if language == "dotenv":
            return "Environment template configuration", [], []

        if self.use_tree_sitter:
            parser = _get_thread_ts_parser(language)
            if parser is not None:
                tree = parser.parse(data)
                root = tree.root_node
                if language == "python":
                    return _ts_extract_python(root, data)
                text = data.decode("utf-8", errors="replace")
                if language in ("typescript", "tsx", "javascript"):
                    return _ts_extract_js_ts(root, data, text)
                if language == "rust":
                    return _ts_extract_rust(root, data, text)
                if language == "go":
                    return _ts_extract_go(root, data, text)
                if language == "markdown":
                    return _ts_extract_markdown(root, data, text)

        text = data.decode("utf-8", errors="replace")
        if language == "python":
            return _fallback_extract_python(text)
        if language in ("typescript", "tsx", "javascript"):
            return _fallback_extract_js_ts(text)
        if language == "rust":
            return _fallback_extract_rust(text)
        if language == "go":
            return _fallback_extract_go(text)
        if language == "markdown":
            return _fallback_extract_markdown(text)
        return _collapse_ws(text, 200), [], []

    def _to_rel_posix(self, file_path: Path) -> str:
        candidate = Path(file_path)
        if not candidate.is_absolute():
            ws_candidate = self.workspace_root / candidate
            if ws_candidate.exists():
                candidate = ws_candidate
        real_file = os.path.realpath(str(candidate))
        return os.path.relpath(real_file, self._workspace_real).replace("\\", "/")

    def _parse_data(
        self, rel_path: str, data: bytes, mtime_ns: int
    ) -> _ParsedRecord:
        content_hash = hashlib.blake2b(data, digest_size=16).hexdigest()
        language = self._detect_language(rel_path)
        is_private = self._is_private_tier1(rel_path, data)
        gist_text, symbols, raw_imports = self._extract_content(
            rel_path, language, data, is_private
        )
        if not gist_text and symbols:
            gist_text = f"{language} defining {', '.join(symbols[:6])}"
        return _ParsedRecord(
            rel_path=rel_path,
            content_hash=content_hash,
            language=language,
            semantic_gist=gist_text,
            symbols=symbols,
            raw_imports=raw_imports,
            mtime_ns=mtime_ns,
            is_private_tier1=is_private,
        )

    def index_file(self, file_path: Path) -> IndexedGist:
        """Parse a single file via tree-sitter/fallback into an IndexedGist."""
        candidate = Path(file_path)
        if not candidate.is_absolute():
            ws_candidate = self.workspace_root / candidate
            if ws_candidate.exists():
                candidate = ws_candidate
        abs_path = os.path.realpath(str(candidate))
        st = os.stat(abs_path)
        rel_path = self._to_rel_posix(Path(abs_path))
        data = _read_bytes(abs_path)
        rec = self._parse_data(rel_path, data, st.st_mtime_ns)
        if self._gists_by_path:
            ws_files = set(self._gists_by_path.keys()) | {rel_path}
            imports = self._resolve_file_imports(
                rel_path,
                rec.language,
                rec.raw_imports,
                ws_files,
                self._build_go_dir_index(ws_files),
            )
        else:
            imports = _dedup_preserve([spec for spec, _ in rec.raw_imports])
        return IndexedGist(
            workspace_hash=self.workspace_hash,
            rel_path=rec.rel_path,
            content_hash=rec.content_hash,
            language=rec.language,
            semantic_gist=rec.semantic_gist,
            symbols=rec.symbols,
            imports=imports,
            fan_in_count=0,
            mtime_ns=rec.mtime_ns,
            is_private_tier1=rec.is_private_tier1,
        )

    def _read_go_module_name(self) -> str | None:
        go_mod = self.workspace_root / "go.mod"
        if not go_mod.is_file():
            return None
        try:
            content = go_mod.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = _RE_GO_MOD.search(content)
        return m.group(1).strip() if m else None

    @staticmethod
    def _build_go_dir_index(ws_files: set[str]) -> dict[str, list[str]]:
        by_dir: dict[str, list[str]] = {}
        for rel in ws_files:
            if rel.endswith(".go") and not rel.endswith("_test.go"):
                d = posixpath.dirname(rel)
                by_dir.setdefault(d, []).append(rel)
        for d in by_dir:
            by_dir[d].sort()
        return by_dir

    @staticmethod
    def _resolve_python_module(
        mod_parts: list[str], ws_files: set[str]
    ) -> str | None:
        if not mod_parts:
            return None
        joined = "/".join(mod_parts)
        for cand in (
            f"{joined}.py",
            f"{joined}.pyi",
            f"{joined}/__init__.py",
            f"src/{joined}.py",
            f"src/{joined}.pyi",
            f"src/{joined}/__init__.py",
        ):
            if cand in ws_files:
                return cand
        return None

    def _resolve_python_import(
        self, src_rel: str, spec: str, names: list[str], ws_files: set[str]
    ) -> list[str]:
        src_dir = posixpath.dirname(src_rel)
        src_parts = [p for p in src_dir.split("/") if p]
        if spec.startswith("."):
            level = len(spec) - len(spec.lstrip("."))
            mod = spec[level:]
            up = level - 1
            if up > len(src_parts):
                return [spec]
            base_parts = src_parts[: len(src_parts) - up]
            mod_parts = base_parts + ([p for p in mod.split(".") if p] if mod else [])
        else:
            mod_parts = [p for p in spec.split(".") if p]

        resolved: list[str] = []
        if names:
            for nm in names:
                sub_hit = self._resolve_python_module(mod_parts + nm.split("."), ws_files)
                if sub_hit is not None:
                    resolved.append(sub_hit)
            if resolved:
                return resolved

        mod_hit = self._resolve_python_module(mod_parts, ws_files)
        if mod_hit is not None:
            return [mod_hit]
        return [spec]

    @staticmethod
    def _resolve_ts_js_import(
        src_rel: str, spec: str, ws_files: set[str]
    ) -> list[str]:
        if spec.startswith(("./", "../")) or spec in (".", ".."):
            src_dir = posixpath.dirname(src_rel)
            base = posixpath.normpath(posixpath.join(src_dir, spec) if src_dir else spec)
            if base == ".." or base.startswith("../"):
                return [spec]
        elif spec.startswith("@/"):
            base = spec[2:]
        else:
            return [spec]

        candidates: list[str] = [base]
        stem, ext = posixpath.splitext(base)
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            for ts_ext in _TS_EXTENSIONS:
                candidates.append(f"{stem}{ts_ext}")
        elif not ext:
            for ts_ext in _TS_EXTENSIONS:
                candidates.append(f"{base}{ts_ext}")
            for idx_ext in _TS_INDEX_EXTENSIONS:
                candidates.append(f"{base}{idx_ext}")

        if spec.startswith("@/"):
            src_base = f"src/{base}"
            candidates.append(src_base)
            for ts_ext in _TS_EXTENSIONS:
                candidates.append(f"{src_base}{ts_ext}")
            for idx_ext in _TS_INDEX_EXTENSIONS:
                candidates.append(f"{src_base}{idx_ext}")

        for cand in candidates:
            if cand in ws_files:
                return [cand]
        return [spec]

    @staticmethod
    def _resolve_rust_import(
        src_rel: str, spec: str, ws_files: set[str]
    ) -> list[str]:
        src_dir = posixpath.dirname(src_rel)
        src_stem = posixpath.splitext(posixpath.basename(src_rel))[0]

        if spec.startswith("mod::"):
            mod_name = spec[5:]
            candidates: list[str] = []
            if src_stem in ("lib", "main", "mod"):
                prefix = f"{src_dir}/" if src_dir else ""
                candidates.extend((f"{prefix}{mod_name}.rs", f"{prefix}{mod_name}/mod.rs"))
            else:
                base = f"{src_dir}/{src_stem}" if src_dir else src_stem
                candidates.extend((f"{base}/{mod_name}.rs", f"{base}/{mod_name}/mod.rs"))
            for cand in candidates:
                if cand in ws_files:
                    return [cand]
            return [spec]

        parts = [p for p in spec.split("::") if p]
        if not parts:
            return [spec]

        if parts[0] == "crate":
            rel_parts = parts[1:]
            roots = [""]
            if src_rel.startswith("src/") or any(f.startswith("src/") for f in ws_files):
                roots.insert(0, "src")
        elif parts[0] == "self":
            rel_parts = parts[1:]
            base = (
                src_dir
                if src_stem in ("lib", "main", "mod")
                else (f"{src_dir}/{src_stem}" if src_dir else src_stem)
            )
            roots = [base]
        elif parts[0] == "super":
            up = 0
            while up < len(parts) and parts[up] == "super":
                up += 1
            rel_parts = parts[up:]
            cur_dir = src_dir
            if src_stem in ("lib", "main", "mod"):
                for _ in range(up):
                    cur_dir = posixpath.dirname(cur_dir)
            else:
                for _ in range(max(0, up - 1)):
                    cur_dir = posixpath.dirname(cur_dir)
            roots = [cur_dir]
        else:
            return [spec]

        for end in range(len(rel_parts), 0, -1):
            sub = "/".join(rel_parts[:end])
            for root in roots:
                prefix = f"{root}/" if root else ""
                for cand in (f"{prefix}{sub}.rs", f"{prefix}{sub}/mod.rs"):
                    if cand in ws_files:
                        return [cand]
        return [spec]

    def _resolve_go_import(
        self,
        src_rel: str,
        spec: str,
        go_dirs: dict[str, list[str]],
    ) -> list[str]:
        if not go_dirs:
            return [spec]
        src_dir = posixpath.dirname(src_rel)
        if spec.startswith(("./", "../")):
            norm = posixpath.normpath(posixpath.join(src_dir, spec) if src_dir else spec)
            if norm in go_dirs:
                return go_dirs[norm]
            return [spec]

        if self._go_module_name and spec.startswith(self._go_module_name):
            sub = spec[len(self._go_module_name) :].lstrip("/")
            if sub in go_dirs:
                return go_dirs[sub]

        parts = [p for p in spec.split("/") if p]
        for start in range(len(parts)):
            cand_dir = "/".join(parts[start:])
            if cand_dir in go_dirs and cand_dir != src_dir:
                return go_dirs[cand_dir]
        return [spec]

    @staticmethod
    def _resolve_markdown_link(
        src_rel: str, spec: str, ws_files: set[str]
    ) -> list[str]:
        if spec.startswith("[[") and spec.endswith("]]"):
            target_name = spec[2:-2].strip().lower()
            for rel in sorted(ws_files):
                if not rel.endswith(".md"):
                    continue
                stem = posixpath.splitext(posixpath.basename(rel))[0].lower()
                if stem == target_name or rel.lower() == target_name:
                    return [rel]
            return [spec]

        src_dir = posixpath.dirname(src_rel)
        norm = posixpath.normpath(posixpath.join(src_dir, spec) if src_dir else spec)
        if norm.startswith("../") or norm == "..":
            return [spec]
        for cand in (norm, f"{norm}.md", f"{norm}/README.md"):
            if cand in ws_files:
                return [cand]
        return [spec]

    def _resolve_file_imports(
        self,
        src_rel: str,
        language: str,
        raw_imports: list[tuple[str, list[str]]],
        ws_files: set[str],
        go_dirs: dict[str, list[str]],
    ) -> list[str]:
        if not raw_imports:
            return []
        resolved: list[str] = []
        for spec, names in raw_imports:
            if language == "python":
                resolved.extend(self._resolve_python_import(src_rel, spec, names, ws_files))
            elif language in ("typescript", "tsx", "javascript"):
                resolved.extend(self._resolve_ts_js_import(src_rel, spec, ws_files))
            elif language == "rust":
                resolved.extend(self._resolve_rust_import(src_rel, spec, ws_files))
            elif language == "go":
                resolved.extend(self._resolve_go_import(src_rel, spec, go_dirs))
            elif language == "markdown":
                resolved.extend(self._resolve_markdown_link(src_rel, spec, ws_files))
            else:
                resolved.append(spec)
        return _dedup_preserve(resolved)

    def _recompute_graph(self) -> list[IndexedGist]:
        ws_files = set(self._records.keys())
        go_dirs = self._build_go_dir_index(ws_files)
        self._go_module_name = self._read_go_module_name() if go_dirs else None

        reverse_edges: dict[str, set[str]] = {rel: set() for rel in ws_files}
        resolved_imports_map: dict[str, list[str]] = {}

        for rel, rec in self._records.items():
            resolved = self._resolve_file_imports(
                rel, rec.language, rec.raw_imports, ws_files, go_dirs
            )
            resolved_imports_map[rel] = resolved
            for target in resolved:
                if target in ws_files and target != rel:
                    reverse_edges[target].add(rel)

        ws_hash = self.workspace_hash
        updated_by_path: dict[str, IndexedGist] = {}
        ordered: list[IndexedGist] = []
        for rel in sorted(ws_files):
            rec = self._records[rel]
            gist = IndexedGist.model_construct(
                workspace_hash=ws_hash,
                rel_path=rec.rel_path,
                content_hash=rec.content_hash,
                language=rec.language,
                semantic_gist=rec.semantic_gist,
                symbols=rec.symbols,
                imports=resolved_imports_map[rel],
                fan_in_count=len(reverse_edges[rel]),
                mtime_ns=rec.mtime_ns,
                is_private_tier1=rec.is_private_tier1,
            )
            updated_by_path[rel] = gist
            ordered.append(gist)

        self._reverse_edges = reverse_edges
        self._gists_by_path = updated_by_path
        self._cached_result = ordered
        return list(ordered)

    def index_workspace(self, incremental: bool = True) -> list[IndexedGist]:
        """Scan workspace files using mtime + content-hash delta skipping."""
        if not self.workspace_root.exists():
            self._dir_mtimes.clear()
            self._file_stats.clear()
            self._records.clear()
            self._gists_by_path.clear()
            self._reverse_edges.clear()
            self._cached_result = []
            return []

        if not incremental:
            self._dir_mtimes.clear()
            self._file_stats.clear()
            self._records.clear()
            self._cached_result = None

        file_stats = self._file_stats
        records = self._records
        excluded_dirs = _EXCLUDED_DIRS
        has_include_res = bool(self._include_res)
        has_exclude_res = bool(self._exclude_res)
        supported_exts = _SUPPORTED_EXTS

        seen_paths: list[str] = []
        pending_parse: list[tuple[str, str, int]] = []
        new_dir_mtimes: dict[str, int] = {}
        any_changed = False

        try:
            new_dir_mtimes[""] = os.stat(self._workspace_real).st_mtime_ns
        except OSError:
            pass

        stack: list[tuple[str, str]] = [(self._workspace_real, "")]
        while stack:
            abs_dir, rel_dir = stack.pop()
            prefix = f"{rel_dir}/" if rel_dir else ""
            try:
                with os.scandir(abs_dir) as it:
                    for entry in it:
                        name = entry.name
                        if entry.is_file(follow_symlinks=False):
                            if not has_include_res and not has_exclude_res:
                                dot_idx = name.rfind(".")
                                if dot_idx == -1 or name[dot_idx:].lower() not in supported_exts:
                                    continue
                                rel_path = prefix + name
                            else:
                                rel_path = prefix + name
                                if not self._is_included_file(rel_path, name):
                                    continue

                            seen_paths.append(rel_path)
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            sig = (st.st_mtime_ns, st.st_size)
                            if (
                                incremental
                                and file_stats.get(rel_path) == sig
                                and rel_path in records
                            ):
                                continue
                            any_changed = True
                            file_stats[rel_path] = sig
                            pending_parse.append((entry.path, rel_path, st.st_mtime_ns))
                        elif entry.is_dir(follow_symlinks=False):
                            if name in excluded_dirs:
                                continue
                            sub_rel = prefix + name
                            if has_exclude_res and self._is_excluded_path(sub_rel, is_dir=True):
                                continue
                            try:
                                new_dir_mtimes[sub_rel] = entry.stat(
                                    follow_symlinks=False
                                ).st_mtime_ns
                            except OSError:
                                pass
                            stack.append((entry.path, sub_rel))
            except OSError:
                continue

        self._dir_mtimes = new_dir_mtimes

        if not any_changed and len(seen_paths) == len(records) and self._cached_result is not None:
            return list(self._cached_result)

        if any_changed or len(seen_paths) != len(records):
            seen_set = set(seen_paths)
            for rel_path in list(records.keys()):
                if rel_path not in seen_set:
                    records.pop(rel_path, None)
                    file_stats.pop(rel_path, None)

        if len(pending_parse) >= 32:
            if self.use_tree_sitter:
                seen_langs = {self._detect_language(item[1]) for item in pending_parse}
                for lang in seen_langs:
                    if lang not in {"dotenv", "text"}:
                        _get_ts_parser(lang)
            workers = min(8, (os.cpu_count() or 4) * 2, len(pending_parse))

            def _read_and_parse(item: tuple[str, str, int]) -> tuple[str, _ParsedRecord]:
                abs_path, rel_path, mtime_ns = item
                return rel_path, self._parse_data(rel_path, _read_bytes(abs_path), mtime_ns)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                for rel_path, rec in executor.map(_read_and_parse, pending_parse):
                    records[rel_path] = rec
        else:
            for abs_path, rel_path, mtime_ns in pending_parse:
                records[rel_path] = self._parse_data(rel_path, _read_bytes(abs_path), mtime_ns)

        return self._recompute_graph()

    def _normalize_target_path(self, target: str) -> str:
        norm = target.replace("\\", "/").strip()
        while norm.startswith("./"):
            norm = norm[2:]
        if norm in self._gists_by_path:
            return norm
        try:
            p = Path(target)
            if p.is_absolute():
                return os.path.relpath(os.path.realpath(str(p)), self._workspace_real).replace(
                    "\\", "/"
                )
        except OSError:
            pass
        return norm

    def compute_blast_radius(self, target_paths: list[str]) -> float:
        """Compute normalized structural blast radius B in [0.0, 1.0] from fan-in edges."""
        if not target_paths:
            return 0.0
        if self._cached_result is None:
            self.index_workspace(incremental=True)

        total_files = len(self._gists_by_path)
        if total_files <= 1:
            return 0.0

        valid_targets = [
            norm
            for raw in target_paths
            if (norm := self._normalize_target_path(raw)) in self._gists_by_path
        ]
        if not valid_targets:
            return 0.0

        impacted: set[str] = set()
        for target in valid_targets:
            visited: set[str] = {target}
            queue: list[str] = [target]
            while queue:
                cur = queue.pop()
                for importer in self._reverse_edges.get(cur, ()):
                    if importer not in visited:
                        visited.add(importer)
                        impacted.add(importer)
                        queue.append(importer)

        if not impacted:
            return 0.0
        return min(1.0, len(impacted) / float(total_files - 1))


__all__ = [
    "AstGistIndexer",
    "check_text_air_gap",
    "is_sensitive_content",
    "is_sensitive_file",
    "is_sensitive_path",
]
