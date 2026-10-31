"""Write the API reference (docs/reference/index.html) from the package's own docstrings.

    python tools/build_reference_page.py        then: python tools/site_chrome.py

The source is read, not imported: the page is the same on every machine, whatever is installed there. It lists
what a module makes public: the functions and classes whose names do not start with an underscore, with their
signatures as written. Nothing is described here that the code does not describe itself.

The Julia package (julia/UDeepCore) is listed the same way: the names it exports, each with the docstring that
stands before its definition in the source. Julia is not run either.
"""
from __future__ import annotations

import ast
import html
import re
from pathlib import Path

from build_profile_page import STYLE

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "udeep"
OUT = ROOT / "docs" / "reference" / "index.html"
REPO = "https://github.com/ultradeeprange/udeep/blob/main/src/udeep"
JULIA = ROOT / "julia" / "UDeepCore" / "src"
JULIA_FILES = ["UDeepCore.jl", "figure.jl", "png.jl"]
JULIA_REPO = "https://github.com/ultradeeprange/udeep/blob/main/julia/UDeepCore"

# in the order a reader meets them: making a figure, then the parts it is made of, then checking and support
MODULES = ["mpl", "view", "mapping", "cmap", "gainmap", "export", "metadata", "validate", "selftest", "scale", "tone", "masks",
           "jnd", "vision", "color", "core", "gpu", "golden"]

EXTRA = """
  .sig { font: 13.5px/1.5 var(--mono); background: var(--panel); border-left: 3px solid var(--tag); padding: 10px 12px;
         margin: 0 0 10px; overflow-x: auto; white-space: pre; border-radius: 0 3px 3px 0; }
  article pre.block { font: 13px/1.5 var(--mono); background: var(--panel); padding: 10px 12px; overflow-x: auto;
                      border-radius: 3px; margin: 0 0 12px; }
  article h3 { font-family: var(--mono); font-size: 16px; margin: 30px 0 8px; }
  article h3 .kind { font: 600 11px var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted);
                     margin-right: 8px; }
  article h4 { font: 600 14px var(--mono); margin: 18px 0 6px; }
  .member { margin-left: 18px; padding-left: 14px; border-left: 1px solid var(--line); }
  .none { color: var(--muted); font-style: italic; }
  .where { font-size: 13px; }
"""


def prose(text: str | None) -> str:
    """A docstring as HTML: paragraphs, with indented lines kept as they are and `code` marked."""
    if not text:
        return '<p class="none">No description in the source.</p>'
    out, lines = [], text.strip("\n").splitlines()
    block: list[str] = []
    kind = None                                                    # "p" or "pre"

    def flush():
        nonlocal block, kind
        if block:
            body = html.escape("\n".join(block))
            if kind == "pre":
                out.append(f'<pre class="block">{body}</pre>')
            else:
                out.append("<p>" + re.sub(r"`([^`\n]+)`", r"<code>\1</code>", body) + "</p>")
        block, kind = [], None

    for line in lines:
        if not line.strip():
            flush()
            continue
        this = "pre" if line.startswith("    ") else "p"
        if kind not in (None, this):
            flush()
        kind = this
        block.append(line[4:] if this == "pre" else line.strip())
    flush()
    return "\n".join(out)


def signature(node: ast.FunctionDef, name: str | None = None) -> str:
    text = f"{name or node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        text += f" -> {ast.unparse(node.returns)}"
    if len(text) <= 100:
        return text
    args = node.args                                               # one argument a line, as the source would wrap it
    parts = []
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for a, d in zip(positional, defaults, strict=True):
        parts.append(ast.unparse(a) + (f"={ast.unparse(d)}" if d is not None and a.annotation is None else
                                       f" = {ast.unparse(d)}" if d is not None else ""))
    if args.vararg:
        parts.append("*" + ast.unparse(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(ast.unparse(a) + (f"={ast.unparse(d)}" if d is not None and a.annotation is None else
                                       f" = {ast.unparse(d)}" if d is not None else ""))
    if args.kwarg:
        parts.append("**" + ast.unparse(args.kwarg))
    tail = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{name or node.name}(\n    " + ",\n    ".join(parts) + f",\n){tail}"


def public(name: str) -> bool:
    return not name.startswith("_")


def module_html(name: str) -> tuple[str, int]:
    path = PACKAGE / f"{name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = [f'<h2 id="{name}">udeep.{name}</h2>',
           f'<p class="where"><a href="{REPO}/{name}.py">src/udeep/{name}.py</a></p>', prose(ast.get_docstring(tree))]
    count = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and public(node.name):
            count += 1
            out += [f'<h3 id="{name}.{node.name}"><span class="kind">function</span>{node.name}</h3>',
                    f'<pre class="sig">{html.escape(signature(node))}</pre>', prose(ast.get_docstring(node))]
        elif isinstance(node, ast.ClassDef) and public(node.name):
            count += 1
            out += [f'<h3 id="{name}.{node.name}"><span class="kind">class</span>{node.name}</h3>',
                    prose(ast.get_docstring(node))]
            members = [m for m in node.body if isinstance(m, ast.FunctionDef) and public(m.name)]
            if members:
                out.append('<div class="member">')
                for m in members:
                    decorators = {ast.unparse(d) for d in m.decorator_list}
                    label = "property" if "property" in decorators else "class method" if "classmethod" in decorators \
                        else "static method" if "staticmethod" in decorators else "method"
                    shown = m.name if label == "property" else signature(m)
                    out += [(f'<h4 id="{name}.{node.name}.{m.name}">{html.escape(m.name)} '
                            f'<span class="kind">{label}</span></h4>'),
                            f'<pre class="sig">{html.escape(shown)}</pre>', prose(ast.get_docstring(m))]
                out.append("</div>")
    return "\n".join(out), count


def top_level(described: str) -> str:
    """What `import udeep` gives, from the package's __all__; a name links to its entry in `described` if it has one."""
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    origin = {a.asname or a.name: node.module.removeprefix("udeep.")
              for node in tree.body if isinstance(node, ast.ImportFrom) for a in node.names}
    names = next(ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets))
    links = ", ".join(f'<a href="#{origin[n]}.{n}"><code>{n}</code></a>'
                      if n in origin and f'id="{origin[n]}.{n}"' in described else f"<code>{n}</code>"
                      for n in sorted(names, key=str.lower))
    return ('<h2 id="udeep">udeep</h2>\n<p>What <code>import udeep</code> gives. The matplotlib interface is its own '
            'module, <a href="#mpl"><code>udeep.mpl</code></a>, imported as <code>import udeep.mpl as hp</code>.</p>\n'
            f"<p>{links}</p>")


def julia_docstrings(text: str) -> list[tuple[str, str]]:
    """(the line that follows a docstring, the docstring) for each docstring that starts a line of a Julia source."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        doc = None
        if line.startswith('"""'):
            end = next(j for j in range(i + 1, len(lines)) if lines[j].startswith('"""'))
            doc, i = "\n".join(lines[i + 1:end]), end
        elif line.startswith('"') and line.rstrip().endswith('"') and len(line.rstrip()) > 1:
            doc = line.rstrip()[1:-1].replace('\\"', '"')
        if doc is not None and i + 1 < len(lines) and lines[i + 1].strip():
            out.append((lines[i + 1], doc))
        i += 1
    return out


def julia_name(definition: str) -> tuple[str, str] | None:
    """(kind, name) of the thing a line of Julia defines, or None if it is not a definition this page lists."""
    m = re.match(r"(?:Base\.@kwdef )?(?:mutable )?struct (\w+)", definition)
    if m:
        return "type", m.group(1)
    m = re.match(r"function (\w+!?)", definition) or re.match(r"(\w+!?)\(.*\) =( |$)", definition)
    return ("function", m.group(1)) if m else None


def julia_prose(doc: str) -> str:
    """A Julia docstring as HTML. A reference to another entry is written as its name alone."""
    return prose(re.sub(r"\[(`[^`]+`)\]\(@ref\)", r"\1", doc))


def julia_html() -> tuple[str, int]:
    """The exported names of UDeepCore, in the order the source defines them."""
    sources = {name: (JULIA / name).read_text(encoding="utf-8").replace("\r\n", "\n") for name in JULIA_FILES}
    exported = set(re.findall(r"[\w!]+", " ".join(re.findall(r"^export (.+)$", sources["UDeepCore.jl"], re.MULTILINE))))
    intro = julia_docstrings(sources["UDeepCore.jl"])[0]
    assert intro[0].startswith("module UDeepCore"), "the module's docstring was expected first"
    intro_text = "\n".join(intro[1].split("\n")[2:])                  # without the line that names the module
    out = ['<h2 id="julia">UDeepCore (Julia)</h2>',
           (f'<p class="where"><a href="{JULIA_REPO}">julia/UDeepCore</a> &middot; its Makie extension is in '
           f'<a href="{JULIA_REPO}/ext/UDeepCoreMakieExt.jl">ext/UDeepCoreMakieExt.jl</a></p>'), julia_prose(intro_text)]
    seen: list[str] = []
    for file in JULIA_FILES:
        for definition, doc in julia_docstrings(sources[file]):
            found = julia_name(definition)
            if found is None or found[1] not in exported or found[1] in seen:
                continue
            kind, name = found
            seen.append(name)
            out += [f'<h3 id="julia.{name}"><span class="kind">{kind}</span>{html.escape(name)}</h3>', julia_prose(doc)]
    missing = sorted(exported - set(seen))
    if missing:
        out.append('<p class="none">Exported with no description in the source: '
                   + ", ".join(f"<code>{html.escape(n)}</code>" for n in missing) + ".</p>")
    return "\n".join(out), len(exported)


def article() -> tuple[str, int]:
    parts, total = [], 0
    for name in MODULES:
        text, count = module_html(name)
        parts.append(text)
        total += count
    modules = "\n".join(parts)
    return top_level(modules) + "\n" + modules + "\n" + julia_html()[0], total


def page() -> str:
    body, total = article()
    toc = ('<a href="#udeep">udeep</a>' + "".join(f'<a href="#{m}">udeep.{m}</a>' for m in MODULES)
           + '<span class="label">Julia</span><a href="#julia">UDeepCore</a>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Reference · UDeep</title>
<meta name="description" content="The udeep Python package and the UDeepCore Julia package: every public function and type, with the description written in the source.">
<link rel="stylesheet" href="../fonts/plex.css">
<style>{STYLE}{EXTRA}</style>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="../index.html">U<b>Deep</b></a>
  <nav aria-label="Site"></nav>
</div></header>
<main><div class="wrap layout">
  <nav class="toc" aria-label="Modules"><span class="label">Python</span>{toc}</nav>
  <article>
    <h1>Reference</h1>
    <p class="source">Written from the docstrings in <code>src/udeep/</code> by <code>tools/build_reference_page.py</code>:
      {total} public functions and classes in {len(MODULES)} Python modules, and the {julia_html()[1]} names the Julia
      package exports, from <code>julia/UDeepCore/src/</code>. The profile says what a figure file means; this page
      says what the code offers.</p>
    {body}
  </article>
</div></main>
<footer><div class="wrap"><p>UDeep · code under BSD-3-Clause.</p></div></footer>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page(), encoding="utf-8")
    print("wrote", OUT, f"({len(page()) // 1024} KiB)")
