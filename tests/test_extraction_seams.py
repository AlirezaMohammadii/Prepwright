"""The seams the ADR 0007 extraction created, asserted rather than remembered.

Three rules, each of which cost real time when it was only a convention.

1. A test may only patch a name on a module that actually defines it. Python
   looks a global up in the module where the CALLER is defined, so patching
   `bridge._read_settings` after that function moved to `prepwright/provider.py`
   changes nothing the consumer sees: the test goes green while testing the real
   file on disk. It happened during the extraction itself, in the subtler form
   where the assignment moved to the new module and the addCleanup restore did
   not, so a junk lambda leaked into every later test in the file.

2. Imports run one way. Nothing inside the package imports bridge. bridge is the
   composition root; a module that reaches back up cannot be imported without
   starting a server.

3. bridge.SCRIPT_DIR and config.SCRIPT_DIR are the same directory. They are
   computed twice and cannot be computed once: bridge needs its own copy before
   any import of the package can happen, because that copy is what puts the
   package on sys.path.
"""

import ast
import glob
import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bridge  # noqa: E402
from prepwright import config as PC  # noqa: E402


def _alias_map(tree):
    """alias used in the file -> importable module name."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                out[a.asname or a.name] = "%s.%s" % (node.module, a.name)
    return out


def _resolve(expr, aliases):
    """A module expression like `bridge` or `bridge.PC` -> the module, or None."""
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    root = expr.id
    if root not in aliases:
        return None
    try:
        obj = importlib.import_module(aliases[root])
    except ImportError:
        return None
    for part in reversed(parts):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _patch_sites():
    """(file, line, module_object, attribute) for every patch a test performs."""
    sites = []
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))):
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        aliases = _alias_map(tree)
        name = os.path.basename(path)
        for node in ast.walk(tree):
            # `mod.attr = value`
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute):
                        mod = _resolve(t.value, aliases)
                        if mod is not None and getattr(mod, "__file__", None):
                            sites.append((name, node.lineno, mod, t.attr))
            # `addCleanup(setattr, mod, "attr", value)`
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "addCleanup"
                    and len(node.args) >= 3
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "setattr"
                    and isinstance(node.args[2], ast.Constant)
                    and isinstance(node.args[2].value, str)):
                mod = _resolve(node.args[1], aliases)
                if mod is not None and getattr(mod, "__file__", None):
                    sites.append((name, node.lineno, mod, node.args[2].value))
    return sites


class APatchOnlyCountsAtItsOwnModule(unittest.TestCase):

    def test_every_patched_name_is_defined_by_the_module_it_is_patched_on(self):
        """Rule 1. A patch on a module that only re-exports the name is a test
        that proves nothing, and it reports success while doing it."""
        found = _patch_sites()
        self.assertGreater(len(found), 0, "the scanner found no patch sites at "
                                          "all, so it is not scanning anything")
        wrong = ["%s:%d patches %s on %s, which does not define it"
                 % (f, line, attr, mod.__name__)
                 for f, line, mod, attr in found if not hasattr(mod, attr)]
        self.assertEqual(wrong, [], "\n".join(wrong))

    def test_the_scanner_would_notice_a_wrong_target(self):
        """The guard above is worth only what its scanner catches, so the
        scanner is checked against a name no module defines."""
        source = ('import bridge\n'
                  'bridge.a_name_no_module_defines = 1\n')
        tree = ast.parse(source)
        aliases = _alias_map(tree)
        target = tree.body[1].targets[0]
        mod = _resolve(target.value, aliases)
        self.assertIs(mod, bridge)
        self.assertFalse(hasattr(mod, target.attr))


class ImportsRunOneWay(unittest.TestCase):

    def test_no_module_in_the_package_imports_bridge(self):
        """Rule 2. bridge is the composition root. A module that imports it
        cannot be loaded without also starting the server it defines."""
        offenders = []
        for path in sorted(glob.glob(os.path.join(ROOT, "prepwright", "*.py"))):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(a.name == "bridge" for a in node.names):
                        offenders.append("%s:%d" % (os.path.basename(path),
                                                    node.lineno))
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "bridge":
                        offenders.append("%s:%d" % (os.path.basename(path),
                                                    node.lineno))
        self.assertEqual(offenders, [], "; ".join(offenders))


class TheTwoScriptDirsAreOneDirectory(unittest.TestCase):

    def test_bridge_and_config_agree_on_where_the_install_is(self):
        """Rule 3. Computed twice, of necessity. Asserted equal here so the
        second copy cannot drift if either file is ever moved."""
        self.assertEqual(bridge.SCRIPT_DIR, PC.SCRIPT_DIR)
        self.assertTrue(os.path.isfile(os.path.join(PC.SCRIPT_DIR, "bridge.py")))


if __name__ == "__main__":
    unittest.main()
