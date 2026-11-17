"""Zero-third-party-dependency hook shim (<450ms fail-open, fail-closed Stage-2A)."""

import os
import re
import sys

DEFAULT_DAEMON_HOST = "127.0.0.1"
DEFAULT_DAEMON_PORT = 8787
SHIM_TIMEOUT_SECONDS = 0.450
VALID_GATES = frozenset({"intake", "firewall", "subagent_governor", "stop_audit"})

_BYPASS_INSTRUCTION = "Include [bypass-gates] in prompt or confirm human override."
_ALLOWED_ENV_FILES = frozenset({".env.example", ".env.sample", ".env.template"})
_VALID_HARNESSES = frozenset({"antigravity", "claude_code", "codex_cli", "mcp", "generic"})

_PATH_KEYS = frozenset(
    {
        "Path",
        "TargetFile",
        "file_path",
        "path",
        "target_file",
        "AbsolutePath",
        "TargetPath",
        "dest",
        "destination",
    }
)
_COMMAND_KEYS = frozenset({"CommandLine", "command", "cmd", "script", "shell_command"})
_CONTENT_KEYS = frozenset(
    {
        "CodeContent",
        "ReplacementContent",
        "content",
        "new_string",
        "new_text",
        "replacement",
        "text",
        "diff_text",
        "patch",
        "diff",
    }
)
_TOOL_CONTAINER_KEYS = ("tool_args", "tool_input", "args", "input", "arguments")
_DIRECT_EDIT_LIST_KEYS = ("edits", "ReplacementChunks", "changes", "files")

_STAGE2A_COMPILED = False


class _Stage2ACompiled:
    """Lazily compiled regexes and pure-Python lookup tables for Stage-2A."""

    def __init__(self) -> None:
        self.env_token_re = re.compile(
            r"""(?:^|[\s"'`=<>@,(;|&{]|[/\\])"""
            r"""(\.env(?:\.[A-Za-z0-9_.*-]+|\*)?)"""
            r"""(?=$|[\s"'`),;|&}>/\\])""",
            re.IGNORECASE,
        )
        self.secret_content_res: tuple[re.Pattern[str], ...] = (
            re.compile(r"-----BEGIN\s+(?:[A-Z0-9 ]+\s+)?PRIVATE KEY-----"),
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|live-|test-)?[A-Za-z0-9]{20,}\b"),
            re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}\b"),
        )
        self.outbound_http_re = re.compile(
            r"(?:^|[;&|(\s])(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b",
            re.IGNORECASE,
        )
        self.exfil_sensitive_target_re = re.compile(
            r"(?:~[/\\]\.(?:ssh|aws|kube)"
            r"|\$HOME[/\\]\.(?:ssh|aws)"
            r"|%USERPROFILE%[/\\]\.(?:ssh|aws)"
            r"|[/\\]\.(?:ssh|aws)[/\\]"
            r"|(?:\b|/)(?:id_rsa|id_ed25519|\.netrc|\.npmrc|\.pypirc)\b"
            r"|\$\{?(?:env:)?[A-Za-z0-9_]*"
            r"(?:SECRET|TOKEN|KEY|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Za-z0-9_]*\}?"
            r"|%[A-Za-z0-9_]*"
            r"(?:SECRET|TOKEN|KEY|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Za-z0-9_]*%)",
            re.IGNORECASE,
        )
        self.pipe_env_exfil_re = re.compile(
            r"\b(?:env|printenv|Get-ChildItem\s+Env:|dir\s+env:)\b"
            r".*\|.*\b(?:curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm|nc)\b",
            re.IGNORECASE,
        )
        self.cmd_split_re = re.compile(r"(?:&&|\|\||[;\n|&])")
        self.token_re = re.compile(r"""[^\s"'`,;|&<>()]+""")
        self.short_rm_flags_re = re.compile(r"^-[A-Za-z]+$")
        self.short_force_re = re.compile(r"^-[A-Za-z]*f[A-Za-z]*$")
        self.drive_root_re = re.compile(r"^[A-Za-z]:[/\\]+\*?$")
        self.drive_path_re = re.compile(r"^[A-Za-z]:[/\\]")
        self.unix_sensitive_abs_re = re.compile(
            r"^/(?:etc|root|var|home|Users|tmp|opt|mnt|proc|sys|boot"
            r"|private|Volumes|srv|usr|bin|sbin|lib)(?:/|$)"
        )
        self.unix_two_seg_abs_re = re.compile(r"^/[^/]+/[^/]+")
        self.mkfs_re = re.compile(
            r"(?:^|[;&|(\s])(?:sudo\s+)?(?:[/\\]\S+[/\\])?mkfs(?:\.[A-Za-z0-9_-]+|\s+-t\b)",
            re.IGNORECASE,
        )
        self.dd_wipe_re = re.compile(
            r"(?:^|[;&|(\s])(?:sudo\s+)?dd\b[^;|&]*"
            r"\bof=/dev/(?:sd|nvme|hd|vd|xvd|mmcblk|disk|loop)",
            re.IGNORECASE,
        )
        self.disk_format_re = re.compile(
            r"(?:^|[;&|(\s])(?:Format-Volume|Clear-Disk)\b",
            re.IGNORECASE,
        )

        # Pure-Python base64 & hex/chr/quote/wildcard obfuscation tables
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        b64_map = {ch: idx for idx, ch in enumerate(alphabet)}
        b64_map["-"] = 62
        b64_map["_"] = 63
        self.b64_map: dict[str, int] = b64_map
        self.b64_token_re = re.compile(
            r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{6,512}={0,2}(?![A-Za-z0-9+/_=-])"
        )
        self.b64_cue_re = re.compile(
            r"(?:base64|b64decode|frombase64|atob|-enc(?:odedcommand)?\b"
            r"|certutil\b[^|;&]*-decode)",
            re.IGNORECASE,
        )
        self.hex_escape_re = re.compile(r"\\(?:x([0-9A-Fa-f]{2})|u00([0-9A-Fa-f]{2}))")
        self.oct_escape_re = re.compile(r"\\(0?[0-7]{2,3})")
        self.url_escape_re = re.compile(r"%([0-9A-Fa-f]{2})")
        self.chr_call_re = re.compile(
            r"(?:\bchr\s*\(\s*(0x[0-9A-Fa-f]+|\d+)\s*\)"
            r"|\[\s*char\s*\]\s*(0x[0-9A-Fa-f]+|\d+))",
            re.IGNORECASE,
        )
        self.chr_seq_re = re.compile(
            r"(?:fromCharCode|bytes|bytearray|chr|char\s*\[\s*\])[^{;\n]{0,24}?[\[(]"
            r"(\s*(?:0x[0-9A-Fa-f]+|\d+)(?:\s*,\s*(?:0x[0-9A-Fa-f]+|\d+)){2,}\s*)[\])]",
            re.IGNORECASE,
        )
        self.int_lit_re = re.compile(r"0x[0-9A-Fa-f]+|\d+", re.IGNORECASE)
        self.sq_concat_re = re.compile(r"b?'([^'\n]{1,128})'\s*\+\s*b?'([^'\n]{1,128})'")
        self.dq_concat_re = re.compile(r'b?"([^"\n]{1,128})"\s*\+\s*b?"([^"\n]{1,128})"')
        self.wildcard_env_re = re.compile(
            r"""(?:^|[\s"'`=<>@,(;|&{/\\])(\.(?:"""
            r"""en(?:\?|\[v\])"""
            r"""|e(?:\?|\[n\])v"""
            r"""|(?:\?|\[e\])nv"""
            r"""|\[e\]\[n\]\[v\]"""
            r""")(?:\.[A-Za-z0-9_.*?-]+)?)(?=$|[\s"'`),;|&}>/\\])""",
            re.IGNORECASE,
        )
        self.hex_candidate_re = re.compile(
            r"(?<![0-9A-Za-z_])(?:"
            r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2}){3,127}"
            r"|(?:[0-9A-Fa-f]{2}){4,128}"
            r")(?![0-9A-Za-z_])"
        )


_STAGE2A: _Stage2ACompiled | None = None


def _ensure_stage2a_compiled() -> _Stage2ACompiled:
    """Compile Stage-2A deterministic security patterns lazily on first check."""
    global _STAGE2A_COMPILED
    global _STAGE2A

    if _STAGE2A is None:
        _STAGE2A = _Stage2ACompiled()
        _STAGE2A_COMPILED = True
    return _STAGE2A


_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh", "dash", "pwsh", "powershell", "cmd"})
_SAFE_DEV_PATHS = frozenset(
    {
        "/dev/null",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/zero",
        "/dev/urandom",
        "/dev/random",
    }
)
_DESTRUCTIVE_UNIX_ROOTS = frozenset(
    {
        "/",
        "/*",
        "//",
        "~",
        "~/",
        "~/*",
        "$HOME",
        "${HOME}",
        "$HOME/",
        "$HOME/*",
        "..",
        "../..",
        "/etc",
        "/usr",
        "/var",
        "/bin",
        "/sbin",
        "/lib",
        "/boot",
        "/root",
        "/home",
    }
)
_GIT_GLOBAL_VALUE_FLAGS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
)
_GIT_PUSH_VALUE_FLAGS = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
_CMD_POS0_TOOLCHAIN = frozenset(
    {
        "python",
        "python3",
        "python.exe",
        "python3.exe",
        "pytest",
        "pytest.exe",
        "git",
        "git.exe",
        "node",
        "node.exe",
        "npm",
        "npm.cmd",
        "npx",
        "npx.cmd",
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        "cmd",
        "cmd.exe",
        "bash",
        "bash.exe",
        "sh",
        "env",
        "ruff",
        "ruff.exe",
        "tsc",
        "tsc.cmd",
        "uv",
        "uv.exe",
    }
)


def parse_shim_args(argv: list[str]) -> tuple[str, str]:
    """Parse --harness and --gate flags using only stdlib primitives."""
    harness: str | None = None
    gate: str | None = None
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--harness":
            if idx + 1 >= len(argv):
                raise ValueError("--harness requires a value.")
            harness = argv[idx + 1]
            idx += 2
        elif arg.startswith("--harness="):
            harness = arg.split("=", 1)[1]
            idx += 1
        elif arg == "--gate":
            if idx + 1 >= len(argv):
                raise ValueError("--gate requires a value.")
            gate = argv[idx + 1]
            idx += 2
        elif arg.startswith("--gate="):
            gate = arg.split("=", 1)[1]
            idx += 1
        else:
            raise ValueError(f"Unknown qg-shim argument: {arg}")

    if not harness:
        raise ValueError("Missing required flag: --harness")
    if not gate or gate not in VALID_GATES:
        expected = sorted(VALID_GATES)
        raise ValueError(f"Invalid or missing --gate {gate!r}; expected one of {expected}.")
    return harness, gate


def _make_receipt(
    rule_id: str,
    remediation: str,
    violated_path: str | None = None,
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "score": 1.0,
        "stage": "deterministic_2a",
        "remediation": remediation,
        "bypass_instruction": _BYPASS_INSTRUCTION,
        "strike_count": 1,
        "escalate_to_human": False,
        "violated_path": violated_path,
    }


def _is_forbidden_env_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == ".env":
        return True
    if lowered.startswith(".env.") or lowered.startswith(".env*"):
        return lowered not in _ALLOWED_ENV_FILES
    return False


def _path_has_forbidden_env(raw_path: str) -> bool:
    parts = raw_path.replace("\\", "/").split("/")
    for part in parts:
        if part and part not in {".", ".."} and _is_forbidden_env_name(part):
            return True
    return False


def _command_has_forbidden_env(command: str) -> str | None:
    s2a = _ensure_stage2a_compiled()
    for match in s2a.env_token_re.finditer(command):
        token = match.group(1)
        if _is_forbidden_env_name(token):
            return token
    return None


def _decode_b64_token(tok: str, b64_map: dict[str, int]) -> str | None:
    """Decode a base64/urlsafe-base64 token in pure Python without stdlib base64."""
    eq_count = len(tok) - len(tok.rstrip("="))
    if eq_count > 2:
        return None
    clean = tok[:-eq_count] if eq_count else tok
    if "=" in clean:
        return None
    n = len(clean)
    if n < 4 or n % 4 == 1:
        return None

    out = bytearray()
    full_end = n - (n % 4)
    idx = 0
    while idx < full_end:
        v0 = b64_map.get(clean[idx], -1)
        v1 = b64_map.get(clean[idx + 1], -1)
        v2 = b64_map.get(clean[idx + 2], -1)
        v3 = b64_map.get(clean[idx + 3], -1)
        if (v0 | v1 | v2 | v3) < 0:
            return None
        acc = (v0 << 18) | (v1 << 12) | (v2 << 6) | v3
        out.append((acc >> 16) & 0xFF)
        out.append((acc >> 8) & 0xFF)
        out.append(acc & 0xFF)
        idx += 4

    rem = n - full_end
    if rem == 2:
        v0 = b64_map.get(clean[full_end], -1)
        v1 = b64_map.get(clean[full_end + 1], -1)
        if (v0 | v1) < 0:
            return None
        out.append(((v0 << 6) | v1) >> 4)
    elif rem == 3:
        v0 = b64_map.get(clean[full_end], -1)
        v1 = b64_map.get(clean[full_end + 1], -1)
        v2 = b64_map.get(clean[full_end + 2], -1)
        if (v0 | v1 | v2) < 0:
            return None
        acc = (v0 << 12) | (v1 << 6) | v2
        out.append((acc >> 10) & 0xFF)
        out.append((acc >> 2) & 0xFF)

    if len(out) >= 8 and len(out) % 2 == 0 and all(b == 0 for b in out[1::2]):
        raw_bytes = bytes(out[0::2])
    else:
        raw_bytes = bytes(out)

    if not raw_bytes:
        return None
    try:
        decoded = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None

    for ch in decoded:
        code = ord(ch)
        if not ((32 <= code <= 126) or ch in "\t\r\n"):
            return None
    return decoded


def _check_decoded_for_env(decoded: str) -> str | None:
    stripped = decoded.strip().strip("\"'")
    if not stripped:
        return None
    if _is_forbidden_env_name(stripped) or _path_has_forbidden_env(stripped):
        return stripped
    return _command_has_forbidden_env(decoded)


def _unescape_and_collapse(command: str, s2a: _Stage2ACompiled) -> str:
    text = command
    if "\\" in text:
        if "\\x" in text or "\\u00" in text:
            def _repl_hex(m: re.Match[str]) -> str:
                val = int(m.group(1) or m.group(2), 16)
                return chr(val) if 32 <= val <= 126 else m.group(0)

            text = s2a.hex_escape_re.sub(_repl_hex, text)
        text = s2a.oct_escape_re.sub(
            lambda m: chr(v) if 32 <= (v := int(m.group(1), 8)) <= 126 else m.group(0),
            text,
        )
    if "%" in text:
        text = s2a.url_escape_re.sub(
            lambda m: chr(v) if 32 <= (v := int(m.group(1), 16)) <= 126 else m.group(0),
            text,
        )
    if "chr" in text.lower() or "char" in text.lower():
        def _repl_chr(m: re.Match[str]) -> str:
            raw = m.group(1) or m.group(2)
            val = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
            if 32 <= val <= 126 and chr(val) not in "'\\":
                return f"'{chr(val)}'"
            return m.group(0)

        text = s2a.chr_call_re.sub(_repl_chr, text)

    if "'" in text and "+" in text:
        for _ in range(6):
            nxt = s2a.sq_concat_re.sub(r"'\1\2'", text)
            if nxt == text:
                break
            text = nxt
    if '"' in text and "+" in text:
        for _ in range(6):
            nxt = s2a.dq_concat_re.sub(r'"\1\2"', text)
            if nxt == text:
                break
            text = nxt
    return text


def _command_has_obfuscated_env(command: str) -> str | None:
    """Detect base64, hex, chr(), escape, quote-split, and wildcard .env obfuscations."""
    s2a = _ensure_stage2a_compiled()

    # 1. Wildcard .env globs (e.g. cat .en?, cat .[e]nv)
    if "." in command and ("?" in command or "[" in command):
        for w_match in s2a.wildcard_env_re.finditer(command):
            w_tok = w_match.group(1)
            if not w_tok.lower().endswith((".example", ".sample", ".template")):
                return w_tok

    # 2. Shell inline quote splitting (e.g. cat '.'env, cat .e'n'v, cat ".e""nv")
    if "'" in command or '"' in command:
        for raw_word in command.split():
            if ("'" in raw_word or '"' in raw_word) and "." in raw_word:
                dequoted = raw_word.replace("'", "").replace('"', "").strip(";,|&()<>")
                if _is_forbidden_env_name(dequoted) or _path_has_forbidden_env(dequoted):
                    return dequoted

    # 3. Escape sequences (\x2e, \u002e, \056, %2e), chr()/char, and '+' string concatenation
    collapsed = _unescape_and_collapse(command, s2a)
    if collapsed != command:
        hit = _command_has_forbidden_env(collapsed)
        if hit is not None:
            return hit

    # 4. Sequence codepoint calls: fromCharCode(46,101,110,118), bytes([46,101,110,118])
    low_cmd = command.lower()
    if any(k in low_cmd for k in ("fromcharcode", "bytes", "bytearray", "chr", "char")):
        for seq_m in s2a.chr_seq_re.finditer(command):
            chars: list[str] = []
            for lit_m in s2a.int_lit_re.finditer(seq_m.group(1)):
                lit = lit_m.group(0)
                val = int(lit, 16) if lit.lower().startswith("0x") else int(lit, 10)
                if 32 <= val <= 126:
                    chars.append(chr(val))
            if chars:
                seq_hit = _check_decoded_for_env("".join(chars))
                if seq_hit is not None:
                    return seq_hit

    # 5. Contiguous or space-separated hex strings (e.g. bytes.fromhex('2e656e76'), xxd -r -p)
    for hex_m in s2a.hex_candidate_re.finditer(collapsed):
        hex_clean = hex_m.group(0).replace(" ", "").replace("\t", "")
        if len(hex_clean) < 8 or len(hex_clean) % 2 != 0:
            continue
        raw_hex_bytes = bytes(
            int(hex_clean[i : i + 2], 16) for i in range(0, len(hex_clean), 2)
        )
        try:
            hex_decoded = raw_hex_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if all((32 <= ord(c) <= 126) or c in "\t\r\n" for c in hex_decoded):
            hex_hit = _check_decoded_for_env(hex_decoded)
            if hex_hit is not None:
                return hex_hit

    # 6. Pure-Python Base64 / URL-safe Base64 / UTF-16LE Base64 tokens
    b64_map = s2a.b64_map
    for b64_m in s2a.b64_token_re.finditer(collapsed):
        decoded = _decode_b64_token(b64_m.group(0), b64_map)
        if decoded is not None:
            b64_hit = _check_decoded_for_env(decoded)
            if b64_hit is not None:
                return b64_hit

    return None


_WS_REALPATH_CACHE: dict[tuple[str, str], tuple[str, str]] = {}
_TOKEN_CACHE: dict[str, tuple[int, int, str]] = {}


def _fast_realpath(path: str, known_real_base: str | None = None) -> str:
    norm = os.path.normpath(path)
    if known_real_base is not None and norm == known_real_base:
        return known_real_base
    if os.path.lexists(norm):
        return os.path.realpath(norm)
    tail: list[str] = []
    cur = norm
    while cur:
        if known_real_base is not None and cur == known_real_base:
            return os.path.join(known_real_base, *reversed(tail)) if tail else known_real_base
        if os.path.lexists(cur):
            break
        parent, base = os.path.split(cur)
        if parent == cur:
            break
        if base:
            tail.append(base)
        cur = parent
    resolved = os.path.realpath(cur) if cur else norm
    if tail:
        return os.path.join(resolved, *reversed(tail))
    return resolved


def _resolve_within_workspace(
    raw_path: str,
    ws_base: str,
    ws_norm: str,
) -> tuple[bool, str]:
    expanded = os.path.expanduser(raw_path.strip())
    if os.sep == "/" and "\\" in expanded:
        expanded = expanded.replace("\\", "/")
    if (
        os.path.isabs(expanded)
        or expanded.startswith(("/", "\\"))
        or bool(os.path.splitdrive(expanded)[0])
    ):
        target_real = _fast_realpath(expanded)
    elif ".." in expanded:
        parts = [p for p in expanded.replace("\\", "/").split("/") if p and p != "."]
        if ".." in parts:
            cur = ws_base
            for part in parts:
                if part == "..":
                    if cur != ws_base and os.path.lexists(cur):
                        cur = os.path.realpath(cur)
                    cur = os.path.normpath(os.path.join(cur, ".."))
                else:
                    nxt = os.path.join(cur, part)
                    if os.path.lexists(nxt):
                        cur = nxt
                    else:
                        if cur != ws_base and os.path.lexists(cur):
                            cur = os.path.realpath(cur)
                        cur = os.path.join(cur, part)
            if cur != ws_base and os.path.lexists(cur):
                target_real = os.path.realpath(cur)
            else:
                target_real = _fast_realpath(cur, known_real_base=ws_base)
        else:
            joined = os.path.join(ws_base, expanded)
            target_real = _fast_realpath(joined, known_real_base=ws_base)
    else:
        rel_clean = expanded.replace("\\", "/").strip("/")
        parts = [p for p in rel_clean.split("/") if p and p != "."]
        cur = ws_base
        deepest_existing = ws_base
        tail_idx = len(parts)
        for idx, part in enumerate(parts):
            cur = os.path.join(cur, part)
            if os.path.lexists(cur):
                deepest_existing = cur
            else:
                tail_idx = idx
                break
        if deepest_existing == ws_base:
            target_real = os.path.join(ws_base, *parts) if parts else ws_base
        else:
            real_deepest = os.path.realpath(deepest_existing)
            tail = parts[tail_idx:] if tail_idx < len(parts) else []
            target_real = os.path.join(real_deepest, *tail) if tail else real_deepest
    target_norm = os.path.normcase(target_real)
    try:
        common = os.path.commonpath([ws_norm, target_norm])
    except ValueError:
        return False, target_real
    return common == ws_norm, target_real


def _resolved_has_forbidden_env(resolved: str, ws_base: str, inside: bool) -> bool:
    if _is_forbidden_env_name(os.path.basename(resolved)):
        return True
    if inside:
        try:
            rel = os.path.relpath(resolved, ws_base)
        except ValueError:
            rel = ""
        if rel and rel != "." and _path_has_forbidden_env(rel):
            return True
    return False


def _extract_tool_inputs(
    payload: dict[str, object],
) -> tuple[list[str], list[str], list[tuple[str | None, str]]]:
    paths: list[str] = []
    commands: list[str] = []
    contents: list[tuple[str | None, str]] = []

    containers: list[object] = []
    for tc_key in ("toolCall", "tool_call"):
        tc = payload.get(tc_key)
        if isinstance(tc, dict):
            for arg_key in ("args", "arguments", "input"):
                if isinstance(tc.get(arg_key), dict):
                    containers.append(tc[arg_key])
    for key in _TOOL_CONTAINER_KEYS:
        val = payload.get(key)
        if isinstance(val, dict):
            containers.append(val)

    visited: set[int] = set()
    stack: list[tuple[object, str | None]] = [(c, None) for c in containers]

    # Also support flat/canonical top-level tool fields without scanning prompt_text or metadata.
    for pk in _PATH_KEYS:
        pv = payload.get(pk)
        if isinstance(pv, str) and pv.strip():
            paths.append(pv.strip())
    top_path = paths[0] if paths else None
    for ck in _COMMAND_KEYS:
        cv = payload.get(ck)
        if isinstance(cv, str) and cv.strip():
            commands.append(cv)
    for tk in _CONTENT_KEYS:
        tv = payload.get(tk)
        if isinstance(tv, str) and tv:
            contents.append((top_path, tv))
    touched = payload.get("touched_files")
    if isinstance(touched, list):
        for item in touched:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
    for list_key in _DIRECT_EDIT_LIST_KEYS:
        lv = payload.get(list_key)
        if isinstance(lv, (list, dict)):
            stack.append((lv, top_path))

    while stack:
        node, nearest_path = stack.pop()
        obj_id = id(node)
        if obj_id in visited:
            continue
        visited.add(obj_id)

        if isinstance(node, dict):
            local_path = nearest_path
            for pk in _PATH_KEYS:
                pv = node.get(pk)
                if isinstance(pv, str) and pv.strip():
                    stripped = pv.strip()
                    paths.append(stripped)
                    local_path = stripped
            for k, v in node.items():
                if k in _COMMAND_KEYS and isinstance(v, str) and v.strip():
                    commands.append(v)
                elif k in _CONTENT_KEYS and isinstance(v, str) and v:
                    contents.append((local_path, v))
                elif k == "touched_files" and isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item.strip():
                            paths.append(item.strip())
                elif isinstance(v, (dict, list)):
                    stack.append((v, local_path))
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    stack.append((item, nearest_path))

    return paths, commands, contents


def _unwrap_shell_subcommands(command: str) -> list[str]:
    s2a = _ensure_stage2a_compiled()
    segments = [seg.strip() for seg in s2a.cmd_split_re.split(command) if seg.strip()]
    out: list[str] = []
    for seg in segments:
        out.append(seg)
        tokens = s2a.token_re.findall(seg)
        if not tokens:
            continue
        cmd_name = os.path.basename(tokens[0].replace("\\", "/")).lower()
        if cmd_name in _SHELL_WRAPPERS and len(tokens) >= 2:
            for idx in range(1, len(tokens) - 1):
                if tokens[idx].lower() in {"-c", "/c", "-command"}:
                    inner_start = seg.find(tokens[idx]) + len(tokens[idx])
                    inner = seg[inner_start:].strip().strip("\"'")
                    if inner:
                        out.extend(s2a.cmd_split_re.split(inner))
                    break
    return [s.strip() for s in out if s.strip()]


def _is_destructive_command(command: str) -> bool:
    s2a = _ensure_stage2a_compiled()
    if (
        s2a.mkfs_re.search(command)
        or s2a.dd_wipe_re.search(command)
        or s2a.disk_format_re.search(command)
    ):
        return True

    for seg in _unwrap_shell_subcommands(command):
        tokens = [t.strip("\"'") for t in s2a.token_re.findall(seg)]
        if not tokens:
            continue
        idx = 0
        while idx < len(tokens) and tokens[idx].lower() in {"sudo", "env", "command"}:
            idx += 1
        if idx >= len(tokens):
            continue

        cmd_base = os.path.basename(tokens[idx].replace("\\", "/")).lower()
        rest = tokens[idx + 1 :]

        if cmd_base == "rm":
            has_recursive = False
            has_force = False
            targets: list[str] = []
            for arg in rest:
                if arg == "--":
                    continue
                if arg in {"-r", "-R", "--recursive"}:
                    has_recursive = True
                elif arg in {"-f", "--force"}:
                    has_force = True
                elif s2a.short_rm_flags_re.match(arg):
                    if "r" in arg or "R" in arg:
                        has_recursive = True
                    if "f" in arg:
                        has_force = True
                elif arg.startswith("-"):
                    continue
                else:
                    targets.append(arg)
            if has_recursive and has_force:
                for tgt in targets:
                    norm_tgt = (
                        tgt.rstrip("/") if len(tgt) > 1 and tgt not in {"/*", "//"} else tgt
                    )
                    if (
                        tgt in _DESTRUCTIVE_UNIX_ROOTS
                        or norm_tgt in _DESTRUCTIVE_UNIX_ROOTS
                        or s2a.drive_root_re.match(tgt)
                    ):
                        return True

        elif cmd_base in {"remove-item", "ri"}:
            lowered = [t.lower() for t in rest]
            has_recurse = any(t in {"-recurse", "-r"} for t in lowered)
            has_force = any(t in {"-force", "-fo"} for t in lowered)
            if has_recurse and has_force:
                for tok in rest:
                    val = tok.split("=", 1)[-1].strip("\"'")
                    if (
                        s2a.drive_root_re.match(val)
                        or val in {"/", "/*", "~", "~/", "~/*", "$HOME", "${HOME}"}
                        or val.lower().startswith("$env:systemdrive")
                    ):
                        return True

        elif cmd_base in {"rd", "rmdir", "del", "erase"}:
            lowered = [t.lower() for t in rest]
            has_sub = "/s" in lowered
            has_quiet_or_force = ("/q" in lowered) or ("/f" in lowered)
            if has_sub and has_quiet_or_force:
                for tok in rest:
                    if not tok.startswith("/") and s2a.drive_root_re.match(tok):
                        return True

    return False


def _is_force_push_main(command: str) -> bool:
    s2a = _ensure_stage2a_compiled()
    for seg in _unwrap_shell_subcommands(command):
        tokens = [t.strip("\"'") for t in s2a.token_re.findall(seg)]
        if not tokens:
            continue
        idx = 0
        while idx < len(tokens) and tokens[idx].lower() in {"sudo", "env", "command"}:
            idx += 1
        if idx >= len(tokens):
            continue
        cmd_base = os.path.basename(tokens[idx].replace("\\", "/")).lower()
        if cmd_base not in {"git", "git.exe"}:
            continue

        pos = idx + 1
        while pos < len(tokens):
            tok = tokens[pos]
            if tok in _GIT_GLOBAL_VALUE_FLAGS:
                pos += 2
                continue
            if tok.startswith("-"):
                pos += 1
                continue
            break
        if pos >= len(tokens) or tokens[pos] != "push":
            continue

        push_tokens = tokens[pos + 1 :]
        has_force = False
        pos_args: list[str] = []
        p_idx = 0
        while p_idx < len(push_tokens):
            tok = push_tokens[p_idx]
            if tok in _GIT_PUSH_VALUE_FLAGS:
                p_idx += 2
                continue
            if (
                tok in {"-f", "--force", "--force-with-lease", "--force-if-includes"}
                or tok.startswith("--force-with-lease=")
                or s2a.short_force_re.match(tok)
            ):
                has_force = True
                p_idx += 1
                continue
            if tok.startswith("-"):
                p_idx += 1
                continue
            pos_args.append(tok)
            p_idx += 1

        for arg in pos_args:
            if arg.startswith("+"):
                dest = arg[1:].split(":")[-1]
                if dest.startswith("refs/heads/"):
                    dest = dest[len("refs/heads/") :]
                if dest in {"main", "master", ""}:
                    return True

        if has_force:
            if not pos_args:
                return True
            if len(pos_args) == 1:
                target = pos_args[0].lstrip("+").split(":")[-1]
                if target.startswith("refs/heads/"):
                    target = target[len("refs/heads/") :]
                if target in {"main", "master", "origin", "upstream"}:
                    return True
            else:
                for arg in pos_args[1:]:
                    target = arg.lstrip("+").split(":")[-1]
                    if target.startswith("refs/heads/"):
                        target = target[len("refs/heads/") :]
                    if target in {"main", "master"}:
                        return True

    return False


def _get_ws_pair(
    workspace_root: str,
    ws_cache: list[tuple[str, str]],
) -> tuple[str, str]:
    if not ws_cache:
        raw_ws = workspace_root or "."
        cwd = os.getcwd()
        cache_key = (raw_ws, cwd)
        cached = _WS_REALPATH_CACHE.get(cache_key)
        if cached is not None:
            ws_cache.append(cached)
        else:
            ws_expanded = os.path.expanduser(raw_ws)
            ws_base = _fast_realpath(ws_expanded)
            pair = (ws_base, os.path.normcase(ws_base))
            if os.path.isdir(ws_expanded):
                if len(_WS_REALPATH_CACHE) >= 32:
                    _WS_REALPATH_CACHE.clear()
                _WS_REALPATH_CACHE[cache_key] = pair
            ws_cache.append(pair)
    return ws_cache[0]


def _inspect_command_paths(
    command: str,
    workspace_root: str,
    ws_cache: list[tuple[str, str]],
) -> tuple[str, str] | None:
    """Inspect shell tokens for path/symlink escapes and .env symlink targets."""
    s2a = _ensure_stage2a_compiled()
    ws_pair: tuple[str, str] | None = None
    cur_rel_dir = ""

    for seg in _unwrap_shell_subcommands(command):
        raw_tokens = s2a.token_re.findall(seg)
        if not raw_tokens:
            continue

        for idx, raw_tok in enumerate(raw_tokens):
            tok = raw_tok.strip("\"'")
            if not tok or "://" in tok:
                continue

            candidates: list[str] = []
            if "=" in tok and not tok.startswith("="):
                _, rhs = tok.split("=", 1)
                if rhs:
                    candidates.append(rhs.strip("\"'").lstrip("@"))
            else:
                candidates.append(tok.lstrip("@"))

            for cand in candidates:
                if not cand or "://" in cand or cand in _SAFE_DEV_PATHS:
                    continue

                is_dotdot = (
                    cand == ".."
                    or cand.startswith("../")
                    or cand.startswith("..\\")
                    or "/../" in cand
                    or "\\..\\" in cand
                    or cand.endswith("/..")
                    or cand.endswith("\\..")
                )
                is_tilde = cand == "~" or cand.startswith("~/") or cand.startswith("~\\")
                is_drive = bool(s2a.drive_path_re.match(cand))
                is_unix_abs = bool(s2a.unix_sensitive_abs_re.match(cand))
                if not is_unix_abs and os.sep == "/" and s2a.unix_two_seg_abs_re.match(cand):
                    first_seg = cand.split("/", 2)[1]
                    if first_seg and os.path.exists("/" + first_seg):
                        is_unix_abs = True

                if is_dotdot or is_tilde or is_drive or is_unix_abs:
                    if idx == 0 and (is_drive or is_unix_abs) and not is_dotdot:
                        base = os.path.basename(cand.replace("\\", "/")).lower()
                        if base in _CMD_POS0_TOOLCHAIN:
                            continue

                    if ws_pair is None:
                        ws_pair = _get_ws_pair(workspace_root, ws_cache)
                    probe_cand = (
                        f"{cur_rel_dir}/{cand}"
                        if cur_rel_dir and is_dotdot and not (is_drive or is_unix_abs or is_tilde)
                        else cand
                    )
                    inside, resolved = _resolve_within_workspace(
                        probe_cand, ws_pair[0], ws_pair[1]
                    )
                    if _resolved_has_forbidden_env(resolved, ws_pair[0], inside):
                        return "INV_01_SECRET_EXPOSURE", cand
                    if not inside:
                        return "INV_02_CAGE_CONTAINMENT", cand
                    continue

                # Relative path-like command tokens (catches symlinks/junctions in <0.1ms)
                if cand.startswith(("/", "\\", "-", "$", "%")):
                    continue
                if idx == 0 and "/" not in cand and "\\" not in cand:
                    continue
                if any(ch in cand for ch in ("*", "?", ":", "{", "}")):
                    continue
                if idx == 0:
                    base0 = os.path.basename(cand.replace("\\", "/")).lower()
                    if base0 in _CMD_POS0_TOOLCHAIN:
                        continue

                rel_clean = cand.replace("\\", "/")
                while rel_clean.startswith("./"):
                    rel_clean = rel_clean[2:]
                rel_clean = rel_clean.strip("/")
                if not rel_clean or rel_clean == ".":
                    continue

                if ws_pair is None:
                    ws_pair = _get_ws_pair(workspace_root, ws_cache)
                ws_base, ws_norm = ws_pair

                probe_rels = [rel_clean]
                if cur_rel_dir:
                    probe_rels.insert(0, f"{cur_rel_dir}/{rel_clean}")

                for probe_rel in probe_rels:
                    first_part = probe_rel.split("/", 1)[0]
                    if not os.path.lexists(os.path.join(ws_base, first_part)):
                        continue
                    inside, resolved = _resolve_within_workspace(probe_rel, ws_base, ws_norm)
                    if _resolved_has_forbidden_env(resolved, ws_base, inside):
                        return "INV_01_SECRET_EXPOSURE", cand
                    if not inside:
                        return "INV_02_CAGE_CONTAINMENT", cand

        cmd0 = os.path.basename(raw_tokens[0].strip("\"'").replace("\\", "/")).lower()
        if cmd0 in {"cd", "pushd"} and len(raw_tokens) >= 2:
            cd_arg = raw_tokens[1].strip("\"'").replace("\\", "/")
            while cd_arg.startswith("./"):
                cd_arg = cd_arg[2:]
            cd_arg = cd_arg.strip("/")
            if cd_arg and not cd_arg.startswith(("-", "/", "~", "$")):
                cur_rel_dir = f"{cur_rel_dir}/{cd_arg}" if cur_rel_dir else cd_arg

    return None


def _command_has_path_escape(
    command: str,
    workspace_root: str = ".",
) -> str | None:
    hit = _inspect_command_paths(command, workspace_root, [])
    return hit[1] if hit is not None and hit[0] == "INV_02_CAGE_CONTAINMENT" else None


def _has_hardcoded_secret(text: str) -> bool:
    s2a = _ensure_stage2a_compiled()
    for pattern in s2a.secret_content_res:
        if pattern.search(text):
            return True
    return False


def check_stage2a_deterministic_security(
    payload: dict[str, object],
    workspace_root: str,
) -> dict[str, object] | None:
    """Evaluate <2ms offline fail-closed Stage-2A security rules."""
    s2a = _ensure_stage2a_compiled()
    paths, commands, contents = _extract_tool_inputs(payload)

    decoded_cue_commands: list[str] = []
    for cmd in commands:
        if s2a.b64_cue_re.search(cmd):
            collapsed_cmd = _unescape_and_collapse(cmd, s2a)
            for b64_m in s2a.b64_token_re.finditer(collapsed_cmd):
                dec = _decode_b64_token(b64_m.group(0), s2a.b64_map)
                if dec is not None:
                    decoded_cue_commands.append(dec)

    # 1. Destructive filesystem commands (Rule 3)
    for cmd in (*commands, *decoded_cue_commands):
        if _is_destructive_command(cmd):
            return _make_receipt(
                rule_id="STAGE2A_DESTRUCTIVE_COMMAND",
                remediation="Blocked destructive filesystem/disk wipe command.",
                violated_path=None,
            )

    # 2. Force-push to main/master (Rule 4)
    for cmd in (*commands, *decoded_cue_commands):
        if _is_force_push_main(cmd):
            return _make_receipt(
                rule_id="STAGE2A_FORCE_PUSH_MAIN",
                remediation=(
                    "Push to a feature branch and open a pull request "
                    "instead of force-pushing main/master."
                ),
                violated_path=None,
            )

    ws_cache: list[tuple[str, str]] = []

    # 3. Secret exposure: .env access, outbound exfil, and hardcoded secrets (Rules 1 & 5)
    resolved_paths: list[tuple[str, bool]] = []
    for raw_path in paths:
        if _path_has_forbidden_env(raw_path):
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation=(
                    "Do not read or write .env secret files; "
                    "use runtime environment variables or .env.example."
                ),
                violated_path=raw_path,
            )
        ws_base, ws_norm = _get_ws_pair(workspace_root, ws_cache)
        inside, resolved = _resolve_within_workspace(raw_path, ws_base, ws_norm)
        if _resolved_has_forbidden_env(resolved, ws_base, inside):
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation=(
                    "Do not read or write .env secret files; "
                    "use runtime environment variables or .env.example."
                ),
                violated_path=raw_path,
            )
        resolved_paths.append((raw_path, inside))

    pending_cmd_escapes: list[str] = []
    for cmd in commands:
        env_tok = _command_has_forbidden_env(cmd)
        if env_tok is None:
            env_tok = _command_has_obfuscated_env(cmd)
        if env_tok is not None:
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation=(
                    "Do not read, copy, source, or transmit .env secret files "
                    "from shell commands."
                ),
                violated_path=env_tok,
            )
        if s2a.pipe_env_exfil_re.search(cmd) or (
            s2a.outbound_http_re.search(cmd) and s2a.exfil_sensitive_target_re.search(cmd)
        ):
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation=(
                    "Blocked outbound HTTP command referencing credential files "
                    "or secret environment variables."
                ),
                violated_path=None,
            )
        if _has_hardcoded_secret(cmd):
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation="Remove hardcoded private keys or API tokens from shell commands.",
                violated_path=None,
            )
        cmd_path_hit = _inspect_command_paths(cmd, workspace_root, ws_cache)
        if cmd_path_hit is not None:
            if cmd_path_hit[0] == "INV_01_SECRET_EXPOSURE":
                return _make_receipt(
                    rule_id="INV_01_SECRET_EXPOSURE",
                    remediation=(
                        "Do not read, copy, source, or transmit .env secret files "
                        "via symlinks or junctions."
                    ),
                    violated_path=cmd_path_hit[1],
                )
            pending_cmd_escapes.append(cmd_path_hit[1])

    for dec_cmd in decoded_cue_commands:
        dec_escape = _inspect_command_paths(dec_cmd, workspace_root, ws_cache)
        if dec_escape is not None:
            if dec_escape[0] == "INV_01_SECRET_EXPOSURE":
                return _make_receipt(
                    rule_id="INV_01_SECRET_EXPOSURE",
                    remediation="Blocked encoded .env access from shell command.",
                    violated_path=dec_escape[1],
                )
            pending_cmd_escapes.append(dec_escape[1])

    for content_path, text in contents:
        if _has_hardcoded_secret(text):
            return _make_receipt(
                rule_id="INV_01_SECRET_EXPOSURE",
                remediation=(
                    "Remove hardcoded private keys or API tokens; "
                    "load credentials via environment variables."
                ),
                violated_path=content_path,
            )

    # 4. Workspace cage containment (Rule 2)
    for raw_path, inside in resolved_paths:
        if not inside:
            return _make_receipt(
                rule_id="INV_02_CAGE_CONTAINMENT",
                remediation="Keep all file paths strictly inside the workspace root directory.",
                violated_path=raw_path,
            )

    if pending_cmd_escapes:
        return _make_receipt(
            rule_id="INV_02_CAGE_CONTAINMENT",
            remediation=(
                "Keep shell file arguments strictly inside the workspace root directory."
            ),
            violated_path=pending_cmd_escapes[0],
        )

    return None


def _decode_chunked_body(raw: bytes) -> bytes:
    out = bytearray()
    pos = 0
    total = len(raw)
    while pos < total:
        line_end = raw.find(b"\r\n", pos)
        if line_end == -1:
            break
        size_token = raw[pos:line_end].split(b";", 1)[0].strip()
        chunk_size = int(size_token, 16)
        pos = line_end + 2
        if chunk_size == 0:
            break
        out.extend(raw[pos : pos + chunk_size])
        pos += chunk_size + 2
    return bytes(out)


def forward_to_daemon(
    event_payload: dict[str, object],
    timeout_s: float = SHIM_TIMEOUT_SECONDS,
    *,
    host: str | None = None,
    port: int | None = None,
    token_path: str | None = None,
) -> dict[str, object] | None:
    """Send canonical GateEvent JSON to 127.0.0.1:8787 with a hard fail-open timeout."""
    import json
    import socket

    resolved_host = host or os.environ.get("QUADGATE_DAEMON_HOST") or DEFAULT_DAEMON_HOST

    if port is not None:
        resolved_port = port
    else:
        env_port = os.environ.get("QUADGATE_DAEMON_PORT")
        if env_port:
            try:
                resolved_port = int(env_port)
            except ValueError:
                resolved_port = DEFAULT_DAEMON_PORT
        else:
            resolved_port = DEFAULT_DAEMON_PORT

    effective_timeout = timeout_s
    if timeout_s == SHIM_TIMEOUT_SECONDS:
        env_timeout_ms = os.environ.get("QUADGATE_SHIM_TIMEOUT_MS")
        if env_timeout_ms:
            try:
                parsed_ms = float(env_timeout_ms)
                if parsed_ms > 0:
                    effective_timeout = parsed_ms / 1000.0
            except ValueError:
                effective_timeout = timeout_s
    if effective_timeout <= 0:
        effective_timeout = SHIM_TIMEOUT_SECONDS

    resolved_token_path = (
        token_path
        or os.environ.get("QUADGATE_TOKEN_PATH")
        or os.path.expanduser("~/.quadgate/run.token")
    )
    token = ""
    try:
        st = os.stat(resolved_token_path)
        cached_tok = _TOKEN_CACHE.get(resolved_token_path)
        if (
            cached_tok is not None
            and cached_tok[0] == st.st_mtime_ns
            and cached_tok[1] == st.st_size
        ):
            token = cached_tok[2]
        else:
            with open(resolved_token_path, "r", encoding="utf-8") as fh:
                token = fh.read().strip().replace("\r", "").replace("\n", "")
            if len(_TOKEN_CACHE) >= 16:
                _TOKEN_CACHE.clear()
            _TOKEN_CACHE[resolved_token_path] = (st.st_mtime_ns, st.st_size, token)
    except OSError:
        _TOKEN_CACHE.pop(resolved_token_path, None)
        token = ""

    try:
        body_bytes = json.dumps(event_payload, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None

    request_bytes = (
        f"POST /v1/evaluate HTTP/1.1\r\n"
        f"Host: {resolved_host}:{resolved_port}\r\n"
        f"Authorization: Bearer {token}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii") + body_bytes

    connect_timeout = min(effective_timeout, 0.280)
    try:
        with socket.create_connection(
            (resolved_host, resolved_port),
            timeout=connect_timeout,
        ) as sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            sock.settimeout(effective_timeout)
            sock.sendall(request_bytes)

            buffer = bytearray()
            header_end = -1
            sep_len = 4
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                idx_crlf = buffer.find(b"\r\n\r\n")
                if idx_crlf != -1:
                    header_end = idx_crlf
                    sep_len = 4
                    break
                idx_lf = buffer.find(b"\n\n")
                if idx_lf != -1:
                    header_end = idx_lf
                    sep_len = 2
                    break

            if header_end == -1:
                return None

            header_block = bytes(buffer[:header_end]).decode("iso-8859-1")
            body_part = bytearray(buffer[header_end + sep_len :])

            header_lines = header_block.splitlines()
            if not header_lines:
                return None
            status_parts = header_lines[0].split(" ", 2)
            if len(status_parts) < 2:
                return None
            try:
                status_code = int(status_parts[1])
            except ValueError:
                return None
            if status_code != 200:
                return None

            content_length: int | None = None
            is_chunked = False
            for line in header_lines[1:]:
                if ":" not in line:
                    continue
                h_name, h_val = line.split(":", 1)
                h_lower = h_name.strip().lower()
                v_clean = h_val.strip()
                if h_lower == "content-length":
                    try:
                        content_length = int(v_clean)
                    except ValueError:
                        content_length = None
                elif h_lower == "transfer-encoding" and "chunked" in v_clean.lower():
                    is_chunked = True

            if content_length is not None and content_length >= 0:
                while len(body_part) < content_length:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body_part.extend(chunk)
                raw_body = bytes(body_part[:content_length])
            elif is_chunked:
                while b"\r\n0\r\n\r\n" not in body_part and not bytes(body_part).startswith(
                    b"0\r\n\r\n"
                ):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body_part.extend(chunk)
                raw_body = _decode_chunked_body(bytes(body_part))
            else:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    body_part.extend(chunk)
                raw_body = bytes(body_part)

        parsed = json.loads(raw_body.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
        return None
    except (OSError, ValueError, UnicodeDecodeError, TypeError):
        return None


def _normalize_harness_name(harness: str) -> str:
    normalized = harness.strip().lower().replace("-", "_")
    if normalized == "codex":
        return "codex_cli"
    if normalized in _VALID_HARNESSES:
        return normalized
    return "generic"


def _normalize_to_gate_event(
    harness: str,
    gate: str,
    payload: dict[str, object],
) -> dict[str, object]:
    norm_harness = _normalize_harness_name(harness)

    raw_session = (
        payload.get("session_id")
        or payload.get("conversationId")
        or payload.get("conversation_id")
        or payload.get("sessionId")
    )
    session_id = str(raw_session).strip() if raw_session is not None else ""
    if not session_id:
        session_id = "default-session"

    turn_index = 1
    for t_key in ("turn_index", "turnIndex", "invocationNum", "executionNum", "stepIdx", "turn"):
        raw_t = payload.get(t_key)
        if raw_t is None or isinstance(raw_t, bool):
            continue
        if isinstance(raw_t, (int, float, str)):
            try:
                turn_index = max(1, int(raw_t))
                break
            except ValueError:
                continue

    ws_from_list: str | None = None
    raw_ws_paths = payload.get("workspacePaths")
    if isinstance(raw_ws_paths, list):
        for entry in raw_ws_paths:
            if isinstance(entry, str) and entry.strip():
                ws_from_list = entry.strip()
                break

    raw_ws = (
        payload.get("workspace_root")
        or payload.get("workspaceRoot")
        or ws_from_list
        or payload.get("cwd")
        or os.environ.get("QUADGATE_WORKSPACE_ROOT")
        or os.getcwd()
    )
    workspace_root = str(raw_ws).strip() if raw_ws is not None else ""
    if not workspace_root:
        workspace_root = os.getcwd() or "."

    tool_name: str | None = None
    tool_args: dict[str, object] = {}
    tool_call = payload.get("toolCall") or payload.get("tool_call")
    if isinstance(tool_call, dict):
        tc_name = tool_call.get("name")
        tool_name = str(tc_name) if isinstance(tc_name, str) and tc_name else None
        raw_args = tool_call.get("args") or tool_call.get("arguments") or tool_call.get("input")
        if isinstance(raw_args, dict):
            tool_args = dict(raw_args)
    else:
        raw_tname = payload.get("tool_name") or payload.get("toolName")
        tool_name = str(raw_tname) if isinstance(raw_tname, str) and raw_tname else None
        for key in _TOOL_CONTAINER_KEYS:
            candidate = payload.get(key)
            if isinstance(candidate, dict):
                tool_args = dict(candidate)
                break

    raw_prompt = (
        payload.get("prompt_text")
        or payload.get("lastUserInput")
        or payload.get("prompt")
        or payload.get("userPrompt")
        or payload.get("message")
    )
    prompt_text = str(raw_prompt) if isinstance(raw_prompt, str) else None

    raw_cage = payload.get("assigned_file_cage")
    assigned_file_cage = (
        [str(x) for x in raw_cage if isinstance(x, str)] if isinstance(raw_cage, list) else None
    )

    raw_tier = payload.get("current_tier")
    current_tier = raw_tier if raw_tier in {"floor", "mid", "frontier"} else None

    raw_strike = payload.get("strike_count", 0)
    strike_count = 0
    if isinstance(raw_strike, (int, float, str)) and not isinstance(raw_strike, bool):
        try:
            strike_count = max(0, int(raw_strike))
        except ValueError:
            strike_count = 0

    raw_touched = payload.get("touched_files")
    touched_files = (
        [str(x) for x in raw_touched if isinstance(x, str)]
        if isinstance(raw_touched, list)
        else []
    )

    raw_diff = payload.get("diff_text")
    diff_text = raw_diff if isinstance(raw_diff, str) else None

    continuation_active = bool(payload.get("continuation_active", False))
    bypass_gates = bool(payload.get("bypass_gates", False)) or (
        prompt_text is not None and "[bypass-gates]" in prompt_text.lower()
    )

    metadata: dict[str, object] = {}
    raw_meta = payload.get("metadata")
    if isinstance(raw_meta, dict):
        metadata.update(raw_meta)
    if payload.get("compact_reset") is True or payload.get("compactReset") is True:
        metadata.setdefault("compact_reset", True)
    hook_event_name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(hook_event_name, str) and hook_event_name:
        metadata.setdefault("hook_event_name", hook_event_name)

    event: dict[str, object] = {
        "gate": gate,
        "session_id": session_id,
        "turn_index": turn_index,
        "workspace_root": workspace_root,
        "harness": norm_harness,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "prompt_text": prompt_text,
        "assigned_file_cage": assigned_file_cage,
        "current_tier": current_tier,
        "strike_count": strike_count,
        "touched_files": touched_files,
        "diff_text": diff_text,
        "continuation_active": continuation_active,
        "bypass_gates": bypass_gates,
        "metadata": metadata,
    }
    return event


def _has_direct_tool_fields(payload: dict[str, object]) -> bool:
    for key in _PATH_KEYS | _COMMAND_KEYS | _CONTENT_KEYS:
        if key in payload:
            return True
    for key in _DIRECT_EDIT_LIST_KEYS:
        if key in payload:
            return True
    return "touched_files" in payload


def _emit_block(norm_harness: str, rule_id: str, remediation: str) -> int:
    import json

    if norm_harness == "antigravity":
        sys.stdout.write(
            json.dumps({"decision": "deny", "reason": f"{rule_id}: {remediation}"}) + "\n"
        )
        return 0
    sys.stderr.write(f"{rule_id}: {remediation}\n")
    return 2


def _emit_fail_open(norm_harness: str) -> int:
    if norm_harness == "antigravity":
        sys.stdout.write("{}\n")
    return 0


def _build_augment_text(verdict: dict[str, object]) -> str:
    parts: list[str] = []
    reason = verdict.get("reason")
    if isinstance(reason, str) and reason.strip():
        parts.append(reason.strip())
    tail_hints = verdict.get("tail_hints")
    if isinstance(tail_hints, list) and tail_hints:
        lines = [f"- {h}" for h in tail_hints if isinstance(h, str) and h.strip()]
        if lines:
            parts.append("[QUADGATE_CONTEXT_HINTS]\n" + "\n".join(lines))
    unaddressed = verdict.get("unaddressed_clauses")
    if isinstance(unaddressed, list) and unaddressed:
        lines = [f"- {c}" for c in unaddressed if isinstance(c, str) and c.strip()]
        if lines:
            parts.append("[QUADGATE_UNADDRESSED]\n" + "\n".join(lines))
    return "\n".join(parts)


def run_shim(harness: str, gate: str, raw_stdin: str) -> int:
    """Execute shim pipeline for a single harness hook invocation."""
    import json

    norm_harness = _normalize_harness_name(harness)
    if gate not in VALID_GATES:
        return _emit_fail_open(norm_harness)

    payload: dict[str, object] = {}
    if raw_stdin.strip():
        try:
            loaded = json.loads(raw_stdin)
            if isinstance(loaded, dict):
                payload = loaded
        except ValueError:
            payload = {}

    canonical_event = _normalize_to_gate_event(norm_harness, gate, payload)
    workspace_root = str(canonical_event["workspace_root"])

    # Stage-2A runs FIRST when tool args/fields or firewall gate exist; fails closed offline.
    if gate == "firewall" or bool(canonical_event["tool_args"]) or _has_direct_tool_fields(payload):
        receipt = check_stage2a_deterministic_security(payload, workspace_root)
        if receipt is not None:
            return _emit_block(
                norm_harness,
                str(receipt.get("rule_id") or "STAGE2A_SECURITY_BLOCK"),
                str(receipt.get("remediation") or "Blocked by QuadGate deterministic security."),
            )

    verdict = forward_to_daemon(canonical_event)
    if verdict is None:
        return _emit_fail_open(norm_harness)

    action = str(verdict.get("action") or verdict.get("verdict") or "allow").strip().lower()

    if action == "allow":
        return _emit_fail_open(norm_harness)

    if action == "block":
        v_receipt = verdict.get("violation_receipt")
        receipt_dict = v_receipt if isinstance(v_receipt, dict) else {}
        rule_id = str(receipt_dict.get("rule_id") or "QUADGATE_BLOCK")
        remediation = str(
            receipt_dict.get("remediation")
            or verdict.get("reason")
            or "Operation blocked by QuadGate policy."
        )
        unaddressed = verdict.get("unaddressed_clauses")
        if isinstance(unaddressed, list) and unaddressed:
            clauses = "; ".join(str(c) for c in unaddressed if isinstance(c, str) and c.strip())
            if clauses and clauses not in remediation:
                remediation = f"{remediation} Unaddressed: {clauses}"
        return _emit_block(norm_harness, rule_id, remediation)

    if action == "augment":
        hint_text = _build_augment_text(verdict)
        if norm_harness == "antigravity":
            out_obj: dict[str, object] = {"decision": "allow"}
            if hint_text:
                out_obj["additionalContext"] = hint_text
            sys.stdout.write(json.dumps(out_obj) + "\n")
        elif hint_text:
            sys.stdout.write(hint_text + "\n")
        return 0

    if action == "modify":
        updated_args: dict[str, object] = {}
        raw_tool_args = canonical_event.get("tool_args")
        if isinstance(raw_tool_args, dict):
            updated_args.update(raw_tool_args)
        verdict_args = verdict.get("updated_tool_args")
        if isinstance(verdict_args, dict):
            updated_args.update(verdict_args)
        updated_model = verdict.get("updated_model")
        if isinstance(updated_model, str) and updated_model:
            if norm_harness == "antigravity":
                updated_args.setdefault("Model", updated_model)
            else:
                updated_args.setdefault("model", updated_model)

        if norm_harness == "antigravity":
            out_obj = {"decision": "allow", "updatedInput": updated_args}
            if isinstance(updated_model, str) and updated_model:
                out_obj["updatedModel"] = updated_model
            sys.stdout.write(json.dumps(out_obj) + "\n")
        elif norm_harness == "claude_code":
            sys.stdout.write(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": updated_args,
                        }
                    }
                )
                + "\n"
            )
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "action": "modify",
                        "updated_tool_args": updated_args,
                        "updated_model": updated_model,
                    }
                )
                + "\n"
            )
        return 0

    if action == "promote":
        updated_model = verdict.get("updated_model")
        target_tier = verdict.get("target_tier")
        reason = verdict.get("reason")
        promo_msg = (
            str(reason)
            if isinstance(reason, str) and reason.strip()
            else f"Promote to tier={target_tier or 'frontier'} model={updated_model or 'pro'}"
        )
        if norm_harness == "antigravity":
            out_obj = {
                "decision": "deny",
                "action": "promote",
                "reason": promo_msg,
            }
            if isinstance(updated_model, str) and updated_model:
                out_obj["updatedModel"] = updated_model
            if isinstance(target_tier, str) and target_tier:
                out_obj["targetTier"] = target_tier
            sys.stdout.write(json.dumps(out_obj) + "\n")
            return 0
        tier_label = target_tier or "frontier"
        model_label = updated_model or "pro"
        sys.stderr.write(
            f"QUADGATE_PROMOTE (tier={tier_label}, model={model_label}): {promo_msg}\n"
        )
        return 2

    return _emit_fail_open(norm_harness)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `qg-shim`."""
    args = sys.argv[1:] if argv is None else argv
    if "-h" in args or "--help" in args:
        sys.stdout.write(
            "usage: qg-shim --harness <antigravity|claude_code|codex_cli|mcp> "
            "--gate <intake|firewall|subagent_governor|stop_audit>\n"
        )
        return 0
    harness, gate = parse_shim_args(args)
    raw_stdin = sys.stdin.read()
    return run_shim(harness=harness, gate=gate, raw_stdin=raw_stdin)


if __name__ == "__main__":
    raise SystemExit(main())
