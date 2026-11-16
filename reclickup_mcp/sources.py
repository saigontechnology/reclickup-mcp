# Content for clickup_doc_write read from files on this computer, so the agent points at a saved draft
# instead of re-typing 10+ KB of markdown into the tool call.
#
# Cowork runs the agent in a sandbox where the user's shared folders appear as /sessions/<name>/mnt/<folder>/...,
# while this server runs on the host. Such paths are mapped back through Claude Desktop's session files
# (local_<uuid>.json: processName, folderMountNames; outputs/ and uploads/ live in <uuid[:8]>/).
import glob, json, os, re, sys

try: from .md2delta import md_to_delta
except ImportError: from md2delta import md_to_delta

MD_EXT = (".md", ".markdown", ".txt")
JSON_EXT = (".json",)
MAX_BYTES = 5 * 1024 * 1024
SANDBOX = re.compile(r"^/sessions/([^/]+)/mnt/([^/]+)(?:/(.*))?$")


def session_roots():
    env = os.environ.get("RECLICKUP_SESSIONS_ROOT")
    if env: return [p for p in env.split(os.pathsep) if p]
    home = os.path.expanduser("~")
    bases = []
    if sys.platform == "darwin":
        bases += [os.path.join(home, "Library", "Application Support", n) for n in ("Claude-3p", "Claude")]
    elif sys.platform == "win32":
        for v in ("APPDATA", "LOCALAPPDATA"):
            if os.environ.get(v): bases += [os.path.join(os.environ[v], n) for n in ("Claude-3p", "Claude")]
        if os.environ.get("LOCALAPPDATA"):       # MSIX install keeps its data inside the package
            bases += glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Packages", "*Claude*", "LocalCache", "Roaming", "Claude*"))
    else:
        bases += [os.path.join(home, ".config", n) for n in ("Claude-3p", "Claude")]
    return [os.path.join(b, "local-agent-mode-sessions") for b in bases]


def map_sandbox_path(path):
    """/sessions/<name>/mnt/<folder>/rest -> the same file on this computer, or None."""
    m = SANDBOX.match(path.replace("\\", "/"))
    if not m: return None
    name, mount, rest = m.group(1), m.group(2), m.group(3) or ""
    for root in session_roots():
        for f in glob.glob(os.path.join(root, "*", "*", "local_*.json")):
            try:
                with open(f, encoding="utf-8") as fh: d = json.load(fh)
            except Exception: continue
            if name not in (d.get("processName"), d.get("vmProcessName")): continue
            if mount in ("outputs", "uploads"):
                sid = os.path.basename(f)[len("local_"):-len(".json")]
                base = os.path.join(os.path.dirname(f), sid[:8], mount)
            else:
                base = next((host for host, mn in (d.get("folderMountNames") or {}).items() if mn == mount), None)
                if base is None:
                    base = next((p for p in d.get("userSelectedFolders") or [] if os.path.basename(p.rstrip("/\\")) == mount), None)
            if base: return os.path.join(base, *rest.split("/")) if rest else base
    return None


def resolve(path):
    p = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    if os.path.isfile(p): return p
    mapped = map_sandbox_path(p)
    if mapped and os.path.isfile(mapped): return mapped
    hint = (" It looks like a Cowork sandbox path and could not be mapped to this computer;" if SANDBOX.match(p.replace("\\", "/"))
            else "")
    raise RuntimeError(f"FILE_NOT_FOUND: {path}.{hint} Pass the file's path on the user's computer (inside a folder "
                       "shared with this session), or pass the content as 'markdown'.")


def read_text(path):
    p = resolve(path)
    if os.path.getsize(p) > MAX_BYTES: raise RuntimeError(f"{path}: file is larger than 5 MB.")
    with open(p, encoding="utf-8-sig") as f: return f.read()


def rows_to_markdown(rows):
    """[{"Rule ID":..,"Rule Name":..,"Specification":..}, ...] -> a pipe table (header = keys of the first row)."""
    keys = list(rows[0].keys())
    def cell(v):
        s = "" if v is None else (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
        return s.replace("\r\n", "\n").replace("|", "\\|").replace("\n", "<br>")
    out = ["| " + " | ".join(cell(k) for k in keys) + " |", "|" + "---|" * len(keys)]
    out += ["| " + " | ".join(cell(r.get(k)) for k in keys) + " |" for r in rows]
    return "\n".join(out)


def json_to_ops(data, path):
    if isinstance(data, dict) and isinstance(data.get("ops"), list): return data["ops"]
    if isinstance(data, dict) and "table-embed" in data: return [{"insert": data}, {"insert": "\n"}]
    if isinstance(data, list) and data and all(isinstance(o, dict) and "insert" in o for o in data): return data
    if isinstance(data, list) and data and all(isinstance(o, dict) for o in data):
        return md_to_delta(rows_to_markdown(data))["ops"]
    for k in ("rows", "rules", "items", "business_rules", "brs"):
        if isinstance(data, dict) and isinstance(data.get(k), list) and data[k]: return json_to_ops(data[k], path)
    raise RuntimeError(f"{path}: JSON must be a Quill delta {{ops:[...]}}, a list of ops, or a list of row objects.")


def load_file(path, skip_title=True):
    """-> (ops, markdown_or_None)"""
    text = read_text(path)
    low = path.lower()
    if low.endswith(JSON_EXT) or (not low.endswith(MD_EXT) and text.lstrip()[:1] in "{["):
        try: data = json.loads(text)
        except ValueError as e: raise RuntimeError(f"{path}: not valid JSON ({e}).")
        return json_to_ops(data, path), None
    return md_to_delta(text, skip_title=skip_title)["ops"], text


def _ends_line(ops):
    if not ops: return ops
    last = ops[-1].get("insert")
    if isinstance(last, str) and last.endswith("\n"): return ops
    return ops + [{"insert": "\n"}]


def renumber_tables(ops):
    """Parts converted separately reuse ids like row-t1r0; give every table its own ids."""
    n = 0
    for o in ops:
        te = o.get("insert", {}).get("table-embed") if isinstance(o.get("insert"), dict) else None
        if not te: continue
        n += 1
        for i, r in enumerate(te.get("rows", [])): r["insert"] = {"id": f"row-t{n}r{i}"}
        for i, c in enumerate(te.get("columns", [])): c["insert"] = {"id": f"column-t{n}c{i}"}
    return ops


def build(args):
    """Tool arguments -> (delta, title). Parts are joined in this order: markdown, markdown_path, delta, delta_path, files."""
    parts, first_md = [], None
    def add_md(text):
        nonlocal first_md
        # Only the first part's "# Title" line names the page; a later file keeps its "# " heading.
        parts.append(md_to_delta(text, skip_title=not parts)["ops"])
        if first_md is None: first_md = text
    if args.get("markdown"): add_md(args["markdown"])
    if args.get("markdown_path"): add_md(read_text(args["markdown_path"]))
    if args.get("delta"): parts.append(json_to_ops(args["delta"], "delta"))
    if args.get("delta_path"): parts.append(json_to_ops(json.loads(read_text(args["delta_path"])), args["delta_path"]))
    files = args.get("files") or []
    if isinstance(files, str): files = [files]
    for f in files:
        ops, md = load_file(f, skip_title=not parts)
        if md is not None and first_md is None: first_md = md
        parts.append(ops)
    if not parts: raise RuntimeError("Pass the content: 'markdown_path' / 'files' (preferred) or 'markdown' / 'delta'.")
    ops = []
    for p in parts: ops += _ends_line(json.loads(json.dumps(p)))
    title = args.get("title")
    if not title and first_md and first_md.lstrip().startswith("# "):
        title = first_md.lstrip().splitlines()[0][2:].replace("**", "").strip()
    return {"ops": renumber_tables(ops)}, title
