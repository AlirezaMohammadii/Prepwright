#!/usr/bin/env python3
"""Find names a module reads but never binds. Run after any deletion.

`py_compile` accepts a module that reads an undefined name: the failure is a
NameError at call time, not a SyntaxError at compile time. A cut boundary that
takes one constant too many therefore passes every compile check and fails the
first time a user reaches that branch. Two such cuts have already happened in
this repository.

This walks the module's own scopes and reports every Load-context name that has
no binding anywhere it could come from: not a builtin, not an import, not an
assignment, parameter, comprehension target, except-alias, with-alias, global,
class or function definition in an enclosing scope.

Usage:
    /usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py

Exit status is 1 when anything is reported, so it can gate a commit.
"""

import ast
import builtins
import sys


def _bound_names(node, into):
    """Every name this statement binds, added to `into`."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            into.add(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            into.add(child.name)
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for a in child.names:
                into.add((a.asname or a.name).split(".")[0])
        elif isinstance(child, ast.ExceptHandler) and child.name:
            into.add(child.name)
        elif isinstance(child, ast.arg):
            into.add(child.arg)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            into.update(child.names)


def scan(path):
    with open(path, "rb") as fh:
        source = fh.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [(getattr(exc, "lineno", 0), "<syntax error>", str(exc))]

    # One flat binding set for the whole module. This deliberately over-accepts
    # across scopes: the target is a name that exists NOWHERE after a cut, not a
    # scoping subtlety. A flat set gives zero false positives, which is what
    # makes the tool usable as a gate.
    bound = set(dir(builtins))
    bound.update({"__file__", "__name__", "__doc__", "__spec__", "__package__",
                  "__builtins__", "__loader__", "__debug__"})
    _bound_names(tree, bound)

    seen, out = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in bound or node.id in seen:
                continue
            seen.add(node.id)
            out.append((node.lineno, node.id, "read but never bound in this module"))
    return sorted(out)


def main(argv):
    paths = argv[1:]
    if not paths:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 2
    total = 0
    for path in paths:
        for lineno, name, why in scan(path):
            print("%s:%d: %s -- %s" % (path, lineno, name, why))
            total += 1
    if total:
        print("\n%d orphaned name(s). A cut boundary took a binding with it."
              % total, file=sys.stderr)
        return 1
    print("no orphaned names in %d file(s)" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
