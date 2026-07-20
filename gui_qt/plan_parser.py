"""Docstring-driven plan parser (AST only — never imports the plan module).

Pure stdlib, no Qt.

**Plan detection (MPE):** MPE plans are plain generator functions with no
``@plan`` decorator, so a plan is any top-level generator function — or, when the
module declares ``__all__``, the names it lists.  Detection descends the function
body but not into nested ``def``s, so a plan whose work lives in a decorated
nested ``inner()`` (``return (yield from inner())``) is still found.

**Parameter form:** each detected plan should document its arguments in this
NumPy-style ``Parameters`` grammar so the GUI can build a form::

    Parameters
    ----------
    <name> : <dtype>[ [<units>]]
        <short name> :: <long description>

* dtype in {str, int, float, bool, choice{a, b, ...}, positions}
* units optional, e.g. [deg], [mm], [s], [1/deg]
* body split on the first ' :: ' -> short label / long tooltip
* default + required come from the signature (no default => required;
  a None default => optional, blank omits the argument)
* args not listed in Parameters (e.g. md, scalers, suspenders) are hidden

Importing a plan module would pull in ophyd devices / ``oregistry`` and attempt
EPICS connections, so this reads the file with the ``ast`` module only.
"""

import ast
import os
import re
from collections import namedtuple

from . import paths as _paths

# ── Paths ──────────────────────────────────────────────────────────────────────
# All path anchors live in :mod:`gui_qt.paths` (derived from the GUI's own
# location, so they stay correct across machines).  USER_DIR points at the real
# MPE plan directory (``instrument/plans/``); which of its files actually show
# up as rows in the plan-runner's file browser is controlled by the
# ``visible_plan_files`` setting in :mod:`config` (edited via the Configuration
# dialog's Plan visibility card), not by this module.

# SRC_DIR is the root the generated "from <module> import <plan>" line is
# resolved against (module = path relative to SRC_DIR).  With SRC_DIR =
# PROJECT_ROOT, instrument/plans/foo.py -> "instrument.plans.foo".
SRC_DIR = _paths.IMPORT_ROOT
USER_DIR = _paths.PLANS_DIR

# File in USER_DIR checked by default on startup.
DEFAULT_PLAN_FILE = "scans_stationary_gui_testing.py"


# ── Docstring / signature parser (AST only — never imports the plan module) ────

# One parsed argument.  default/required/blank_omits come from the SIGNATURE;
# dtype/units/short/long/choices/category come from the DOCSTRING.
#
# ``category`` is only meaningful for the device dtypes: it names the device
# group (e.g. "area_detector", "scaler") the GUI should offer for this field.
ParamSpec = namedtuple(
    "ParamSpec",
    "name dtype units short long default required choices blank_omits category",
    defaults=(None,),  # category defaults to None
)

_NODEFAULT = object()  # sentinel: signature arg with no default (=> required)

# dtypes the form knows how to render.  ``device`` = one device object,
# ``device_list`` = a list of device objects; both emit UNQUOTED names (see
# RawCode) and take an optional ``{category}`` filter.
_KNOWN_DTYPES = {
    "str", "int", "float", "bool", "choice", "positions", "device", "device_list",
}


class RawCode(str):
    """A string that must be emitted **verbatim (unquoted)** in generated code.

    Device-typed fields resolve to real objects in the running session, so the
    command must read ``expose(det=pg6, scalers=[tc32E])`` — bare names — not
    ``det='pg6'``.  The command builder emits ``RawCode`` values as-is and
    everything else through ``repr()``.  Being a ``str`` subclass, it also
    displays and validates like normal text.
    """

    __slots__ = ()


def _literal(node: ast.AST):
    """Best-effort literal value of a default node (no code execution)."""
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        try:
            return ast.unparse(node)  # py3.9+
        except Exception:  # noqa: BLE001
            return None


def _module_all(tree) -> list[str] | None:
    """Return the names listed in a module-level ``__all__``, or None if absent.

    MPE plan modules gate their public plan names with an explicit ``__all__``
    (there is no ``@plan`` decorator).  When present we treat it as the list of
    plans; otherwise we fall back to "every top-level generator function".
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    val = _literal(node.value)
                    if isinstance(val, (list, tuple)):
                        return [str(x) for x in val]
    return None


def _is_generator(node) -> bool:
    """True if `node`'s own body contains a ``yield`` / ``yield from``.

    Descends the function body but NOT into nested ``def``/``lambda`` scopes, so
    a plan whose real work lives in a decorated nested ``inner()`` (a common MPE
    pattern) is still detected via its top-level ``yield from inner()``.
    """
    found = False

    def visit(n) -> None:
        nonlocal found
        for child in ast.iter_child_nodes(n):
            if found:
                return
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a separate scope — its yields don't make `node` a generator
            if isinstance(child, (ast.Yield, ast.YieldFrom)):
                found = True
                return
            visit(child)

    visit(node)
    return found


def _signature(node) -> list[tuple[str, object]]:
    """Ordered (name, default-or-_NODEFAULT) for every argument of `node`."""
    a = node.args
    out: list[tuple[str, object]] = []

    positional = list(getattr(a, "posonlyargs", [])) + list(a.args)
    defaults = list(a.defaults)
    n, nd = len(positional), len(defaults)
    for i, arg in enumerate(positional):
        if i >= n - nd:
            out.append((arg.arg, _literal(defaults[i - (n - nd)])))
        else:
            out.append((arg.arg, _NODEFAULT))

    for arg, dnode in zip(a.kwonlyargs, a.kw_defaults, strict=False):
        out.append((arg.arg, _NODEFAULT if dnode is None else _literal(dnode)))

    return out


def _first_paragraph(doc: str) -> str:
    """Docstring summary: first paragraph, whitespace-collapsed."""
    lines: list[str] = []
    for line in doc.strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return " ".join(lines)


def _parse_parameters(doc: str) -> dict[str, dict]:
    """Parse the NumPy ``Parameters`` section into {name: {typespec, body}}.

    Returns {} when the docstring has no Parameters section.
    """
    lines = doc.splitlines()

    # locate the "Parameters" title + dashed underline
    start = None
    for i in range(len(lines) - 1):
        under = lines[i + 1].strip()
        if lines[i].strip() == "Parameters" and under and set(under) == {"-"}:
            start = i + 2
            break
    if start is None:
        return {}

    # collect body lines until the next section (dashed header) or an
    # ``Example::``-style block at column 0
    body: list[str] = []
    for j in range(start, len(lines)):
        line = lines[j]
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ""
        if line.strip() and nxt and set(nxt) == {"-"}:
            break  # this line is the title of the next section
        if line and not line[0].isspace() and line.strip().endswith("::"):
            break  # e.g. "Example::"
        body.append(line)

    # split body into per-argument entries (header at col 0, body indented)
    entries: dict[str, dict] = {}
    cur: dict | None = None
    for line in body:
        if line and not line[0].isspace():
            m = re.match(r"^(\w+)\s*:\s*(.+?)\s*$", line)
            if m:
                cur = {"typespec": m.group(2), "body": []}
                entries[m.group(1)] = cur
            else:
                cur = None  # a col-0 line that is not "name : type"
        elif cur is not None and line.strip():
            cur["body"].append(line.strip())
    return entries


def _parse_typespec(typespec: str) -> tuple[str, str, list[str], str | None]:
    """Parse a typespec into (dtype, units, choices, category).

    Examples::

        'float [deg]'          -> ('float', 'deg', [], None)
        'choice{a, b}'         -> ('choice', '', ['a', 'b'], None)
        'device{area_detector}'-> ('device', '', [], 'area_detector')
        'device_list{scaler}'  -> ('device_list', '', [], 'scaler')
        'device'               -> ('device', '', [], None)
    """
    units = ""
    m = re.search(r"\[([^\]]*)\]\s*$", typespec)
    if m:
        units = m.group(1).strip()
        typespec = typespec[: m.start()].strip()

    dtype = typespec.strip()
    choices: list[str] = []
    category: str | None = None
    # Brace payload after the dtype keyword: choice{a,b} | device{cat} |
    # device_list{cat}.  choice -> comma list; device* -> single category.
    bm = re.match(r"(choice|device_list|device)\s*\{(.*)\}$", dtype)
    if bm:
        dtype = bm.group(1)
        payload = bm.group(2)
        if dtype == "choice":
            choices = [c.strip() for c in payload.split(",") if c.strip()]
        else:
            category = payload.strip() or None
    return dtype, units, choices, category


def _parse_body(body_lines: list[str]) -> tuple[str, str]:
    """Join body lines, split on the first ' :: ' into (short, long)."""
    text = " ".join(body_lines).strip()
    if "::" in text:
        short, long = text.split("::", 1)
        return short.strip(), long.strip()
    return text, ""


def find_plan_specs(filepath: str) -> dict[str, dict]:
    """AST-parse a .py file; return {plan_name: {summary, params, documented}}.

    ``params`` is an ordered list of :class:`ParamSpec` (signature order,
    documented args only).  Never imports the module.
    """
    try:
        with open(filepath, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=filepath)
    except (SyntaxError, OSError):
        return {}

    all_names = _module_all(tree)

    specs: dict[str, dict] = {}
    for node in tree.body:  # top-level functions only (skip nested inner() defs)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Plan detection (MPE): if the module declares __all__, its listed names
        # are the plans; otherwise treat every top-level generator function as a
        # plan.  Never expose private (_-prefixed) helpers.
        if node.name.startswith("_"):
            continue
        if all_names is not None:
            if node.name not in all_names:
                continue
        elif not _is_generator(node):
            continue

        doc = ast.get_docstring(node) or ""
        doc_meta = _parse_parameters(doc)

        params: list[ParamSpec] = []
        for name, default in _signature(node):
            if name not in doc_meta:
                continue  # undocumented (e.g. md) => hidden
            dtype, units, choices, category = _parse_typespec(
                doc_meta[name]["typespec"]
            )
            short, long = _parse_body(doc_meta[name]["body"])
            required = default is _NODEFAULT
            blank_omits = (not required) and default is None
            params.append(
                ParamSpec(
                    name=name,
                    dtype=dtype,
                    units=units,
                    short=short or name,
                    long=long,
                    default=default,
                    required=required,
                    choices=choices,
                    blank_omits=blank_omits,
                    category=category,
                )
            )

        specs[node.name] = {
            "summary": _first_paragraph(doc),
            "params": params,
            "documented": bool(doc_meta),
        }
    return specs


# ── File-browser utilities ────────────────────────────────────────────────────


def file_to_module(filepath: str, src_dir: str | None = None) -> str:
    """Module path for the generated import line (relative to `src_dir`).

    `src_dir` is the import root the module path is resolved against; when None
    it falls back to :data:`SRC_DIR`.  e.g. with root ``gui/``,
    ``test_plans/test_file.py`` -> ``test_plans.test_file``.
    """
    root = src_dir or SRC_DIR
    rel = os.path.relpath(filepath, root)
    return rel.replace(os.sep, ".").removesuffix(".py")


def scan_user_dir(user_dir: str) -> list[tuple]:
    """Shallow scan; returns (display_name, kind, abs_path, depth).

    ``depth`` is 0 for top-level entries and 1 for files nested one directory
    deep (used by the GUI to indent).
    """
    rows: list[tuple] = []
    try:
        entries = sorted(
            os.scandir(user_dir),
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except OSError:
        return rows
    for entry in entries:
        if entry.name.startswith("__"):
            continue
        if entry.is_dir():
            rows.append((entry.name + "/", "dir", entry.path, 0))
            try:
                for sub in sorted(os.scandir(entry.path), key=lambda e: e.name.lower()):
                    if (
                        sub.is_file()
                        and sub.name.endswith(".py")
                        and not sub.name.startswith("__")
                    ):
                        rows.append((sub.name, "file", sub.path, 1))
            except OSError:
                pass
        elif entry.is_file() and entry.name.endswith(".py"):
            rows.append((entry.name, "file", entry.path, 0))
    return rows
