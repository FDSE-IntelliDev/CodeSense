"""CodeSense -- compiling a natural-language query into a query script over
a codebase.

    natural-language query  --compile-->  QL script (Python)  --run-->  results with evidence

The implementation lives entirely in `codesense.ql`: a script orchestrates a
small set of query operators, all of them ``Frag -> Frag``. See
``docs/design/``.

The pre-rewrite SemCon to SemQL to three-executor implementation is archived
under ``legacy/`` at the repository root and takes no part in the build,
linting or tests.
"""

__version__ = "0.2.0.dev0"

from codesense.index import Index, IndexMeta
from codesense.project import Project
from codesense.search import Hit, SearchResult, search

__all__ = ["Hit", "Index", "IndexMeta", "Project", "SearchResult", "search"]
