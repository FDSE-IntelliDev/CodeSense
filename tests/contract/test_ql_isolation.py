"""Contract: `codesense.ql` is implemented from the design docs and must not
be contaminated by the old implementation.

The requirement was to take care not to be influenced by the original
implementation. Discipline alone will not hold that, so it is enforced
mechanically here: this package may not import any other subpackage under
``codesense``.

When something the old implementation could do is needed, the correct move is
to **reimplement it from the design docs**, or to promote it explicitly to
shared infrastructure (which means allowing it through the whitelist here
first). The complete pre-rewrite implementation is at git tag
``pre-ql-rewrite``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

QL_ROOT = Path(__file__).resolve().parents[2] / "codesense" / "ql"

#: Module prefixes allowed from the codesense namespace. Deliberately only
#: the package itself -- not even config: QL configuration is injected through
#: constructors.
ALLOWED = ("codesense.ql",)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def test_ql_imports_no_subpackage_of_the_old_implementation() -> None:
    offenders: list[str] = []
    for path in sorted(QL_ROOT.rglob("*.py")):
        for module in _imported_modules(path.read_text(encoding="utf-8")):
            if module.startswith("codesense") and not module.startswith(ALLOWED):
                rel = path.relative_to(QL_ROOT.parents[1])
                offenders.append(f"{rel}: import {module}")
    assert not offenders, (
        "codesense/ql/ has been contaminated by the old implementation. "
        "Reimplement from the design docs, or add the module to this test's "
        "ALLOWED whitelist with a stated reason:\n  " + "\n  ".join(offenders)
    )


def test_the_ql_directory_exists_and_is_not_empty() -> None:
    """Stops the test above passing vacuously if the directory is deleted or
    renamed."""
    assert QL_ROOT.is_dir()
    assert list(QL_ROOT.rglob("*.py"))


def test_importing_has_no_side_effects() -> None:
    """Contract: importing a module must not read disk, read configuration or
    touch the network.

    The ways to break this look entirely harmless -- a `load_config()` in a
    module-level constant, a `def f(m=BASE_MODEL)` default argument -- but the
    consequence is that without a config file even the import fails, and pure
    logic needing no configuration becomes untestable along with it.

    This contract earned its keep in the archived implementation (see
    legacy/tests/test_import_purity.py); the new package holds it from day
    one.
    """
    import importlib

    for path in sorted(QL_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        module = "codesense.ql." + str(path.relative_to(QL_ROOT).with_suffix("")).replace("/", ".")
        importlib.import_module(module)  # raising is the failure


def test_ql_depends_on_no_third_party_package() -> None:
    """The live QL layer should use the standard library only.

    A third-party dependency ties "can the tests run" to "is the environment
    fully installed", and this layer is pure logic that needs neither. If one
    genuinely becomes necessary (a store beyond sqlite, say), allow it here
    with a stated reason first.
    """
    stdlib = set(sys.stdlib_module_names)
    outside: list[str] = []
    for path in sorted(QL_ROOT.rglob("*.py")):
        for module in _imported_modules(path.read_text(encoding="utf-8")):
            root = module.split(".")[0]
            if root not in stdlib and not module.startswith("codesense"):
                outside.append(f"{path.name}: {module}")
    assert not outside, "the QL layer has taken a third-party dependency:\n  " + "\n  ".join(
        outside
    )
