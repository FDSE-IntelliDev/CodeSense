"""Building index artifacts from source.

The split against its neighbours:

    codesense.lang        how to *read* a language   (the extension point)
    codesense.text        how words work             (language-neutral)
    codesense.indexing    how to *build* artifacts   (this package)
    codesense.index       what an artifact *is*

The dependency runs one way -- indexing uses lang, text and ql's data types;
none of them knows indexing exists.
"""

from codesense.indexing.expansion import build_expansion_table
from codesense.indexing.graph import GraphBuilder, GraphStats, TypeTable
from codesense.indexing.grounding import GroundingConfig, ground_vocabulary
from codesense.indexing.pipeline import BuildResult, Stats, build_index
from codesense.indexing.postings import PostingTable, declaration_terms

__all__ = [
    "BuildResult",
    "GraphBuilder",
    "GraphStats",
    "GroundingConfig",
    "PostingTable",
    "Stats",
    "TypeTable",
    "build_expansion_table",
    "build_index",
    "declaration_terms",
    "ground_vocabulary",
]
