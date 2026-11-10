#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the single-file `ajail` from src/*.py.

    ./build.py            build ./ajail (and check layering)
    ./build.py --check    check layering only

Why fragments, not packages: ajail is installed as one root-owned file (/usr/local/bin/ajail, named in the binfmt_misc
rule), so the release stays one file. The source is split so an auditor can read it in layers:

    00_config  10_util  20_core  30_host  40_images  50_run  60_desktop  70_commands

LAYER RULE (enforced here): a fragment may only use names defined in itself or in a LOWER-numbered fragment.
So the security core (20_core) depends on nothing but config and util, and nothing above it can be pulled in by accident.
"""
import ast, builtins, glob, os, stat, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRAGS = sorted(glob.glob(os.path.join(HERE, "src", "[0-9][0-9]_*.py")))
OUT = os.path.join(HERE, "ajail")

def top_names(tree):
    names = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)): names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in n.names}
        else:
            for x in ast.walk(n):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store) and x in getattr(n, "targets", []): names.add(x.id)
    return names

def local_binds(fn):
    b = {a.arg for a in ast.walk(fn.args) if isinstance(a, ast.arg)}
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)): b.add(x.id)
        elif isinstance(x, (ast.FunctionDef, ast.ClassDef)) and x is not fn: b.add(x.name)
        elif isinstance(x, ast.ExceptHandler) and x.name: b.add(x.name)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            b |= {(a.asname or a.name).split(".")[0] for a in x.names}
    return b

def loads(node, bound=frozenset()):
    """Names read in NODE that are not bound locally (function by function, so locals can shadow globals)."""
    out = set()
    def visit(n, bound):
        if isinstance(n, (ast.FunctionDef, ast.Lambda)):
            if isinstance(n, ast.FunctionDef):
                for d in n.decorator_list + n.args.defaults + [d for d in n.args.kw_defaults if d]: visit(d, bound)
                inner = bound | local_binds(n)
                for c in n.body: visit(c, inner)
            else:
                inner = bound | {a.arg for a in n.args.args}
                visit(n.body, inner)
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound: out.add(n.id)
        for c in ast.iter_child_nodes(n): visit(c, bound)
    visit(node, set(bound))
    return out

def check_layers():
    trees = [(os.path.basename(f), ast.parse(open(f).read(), f)) for f in FRAGS]
    defined = [top_names(t) for _, t in trees]
    bad = []
    for i, (name, tree) in enumerate(trees):
        ok = set(dir(builtins)).union(*defined[: i + 1])
        later = {}
        for j in range(i + 1, len(trees)):
            for nm in defined[j]: later.setdefault(nm, trees[j][0])
        for n in tree.body:
            for nm in sorted(loads(n)):
                if nm not in ok and nm in later: bad.append(f"{name}: uses '{nm}' which is defined in higher layer {later[nm]}")
    return sorted(set(bad))

def render():
    """The text of the single-file ajail, built from the fragments."""
    text = "".join(open(f).read().rstrip("\n") + "\n\n\n" for f in FRAGS).rstrip("\n") + "\n"
    ast.parse(text)
    return text

def build():
    open(OUT, "w").write(render())
    os.chmod(OUT, os.stat(OUT).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

if __name__ == "__main__":
    bad = check_layers()
    if bad:
        print("LAYER VIOLATIONS:\n  " + "\n  ".join(bad), file=sys.stderr); sys.exit(1)
    print(f"layers ok ({len(FRAGS)} fragments)")
    if "--check" not in sys.argv:
        build(); print(f"built {OUT} ({sum(1 for _ in open(OUT))} lines)")
