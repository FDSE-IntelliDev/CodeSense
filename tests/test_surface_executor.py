import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from executor.surface_executor import SurfaceExecutor, run_surface_search
from filters.type_filter import filter_symbols_by_type
from query_processing.planners.surface_planner import SurfacePlanner
from search.full_term_matcher import FullTermMatcher
from search.invert_index_search import search_symbols_by_terms


SYMBOLS = {
    "1": {"symbol_id": 1, "name": "userLogin", "type": "method", "file": "Auth.java"},
    "2": {"symbol_id": 2, "name": "loginLogout", "type": "method", "file": "Auth.java"},
    "3": {"symbol_id": 3, "name": "userAccount", "type": "method", "file": "User.java"},
    "4": {"symbol_id": 4, "name": "logout", "type": "method", "file": "Auth.java"},
}

TERM_HITS = {
    "login": ["1", "2"],
    "auth": ["1"],
    "user": ["1", "3"],
    "account": ["3"],
    "logout": ["2", "4"],
    "signout": ["4"],
}


def fake_term_search(*_args, terms, **_kwargs):
    matched_ids = []
    matched_terms_by_id = {}
    details = []
    for term in terms:
        details.append(
            {
                "term": term,
                "source_terms": [term],
                "keyword": term,
                "matched_subtokens": [term],
            }
        )
        for symbol_id in TERM_HITS.get(term, []):
            if symbol_id not in matched_ids:
                matched_ids.append(symbol_id)
            matched_terms_by_id.setdefault(symbol_id, []).append(term)
    return {
        "symbols": [dict(SYMBOLS[symbol_id]) for symbol_id in matched_ids],
        "matched_subtokens_by_symbol_id": matched_terms_by_id,
        "detail": details,
    }


class SurfaceExecutorTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tmp_dir.name) / "project"
        self.query_dir = Path(self.tmp_dir.name) / "query"
        self.project_dir.mkdir()
        self.query_dir.mkdir()
        (self.project_dir / "invert_index.json").write_text("{}", encoding="utf-8")
        (self.project_dir / "ngramed_symbol.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _executor(self):
        return SurfaceExecutor(
            output_dir=str(self.query_dir),
            project_output_dir=str(self.project_dir),
        )

    @patch("executor.surface_executor.search_symbols_by_terms", side_effect=fake_term_search)
    def test_executes_term_or_and_group_and_zero_with_evidence(self, _search):
        plan = SurfacePlanner().plan(
            [
                {
                    "match_kind": "code_element",
                    "code_element_type": "function",
                    "keyword_groups": [
                        {
                            "group_id": "k1",
                            "property": "include",
                            "keywords": ["login"],
                            "synonyms": ["auth"],
                        },
                        {
                            "group_id": "k2",
                            "property": "include",
                            "keywords": ["user"],
                            "synonyms": ["account"],
                        },
                    ],
                    "group_logic": [
                        {
                            "groups": ["k1", "k2"],
                            "graph_scope": ["call"],
                            "default_hop_count": 0,
                        }
                    ],
                }
            ],
            raw_query="Find the user login entry point",
        ).to_dict()

        executor = self._executor()
        try:
            results = executor.execute(plan)
        finally:
            executor.close()

        self.assertEqual([item["symbol_id"] for item in results], [1])
        evidence = executor.execution_report["evidence_by_symbol_id"]["1"]
        self.assertEqual(evidence["condition_ids"], ["surface_0"])
        self.assertEqual(
            evidence["coverage"]["surface_0"]["k1"]["term"],
            "login",
        )
        self.assertEqual(
            evidence["coverage"]["surface_0"]["k1"]["matched_term"],
            "login",
        )

    @patch("executor.surface_executor.search_symbols_by_terms", side_effect=fake_term_search)
    def test_subtracts_exclude_group_inside_condition(self, _search):
        plan = SurfacePlanner().plan(
            [
                {
                    "match_kind": "code_element",
                    "code_element_type": "method",
                    "keyword_groups": [
                        {
                            "group_id": "k1",
                            "property": "include",
                            "keywords": ["login"],
                        },
                        {
                            "group_id": "k2",
                            "property": "exclude",
                            "keywords": ["logout"],
                        },
                    ],
                }
            ]
        ).to_dict()

        executor = self._executor()
        try:
            results = executor.execute(plan)
        finally:
            executor.close()
        self.assertEqual([item["symbol_id"] for item in results], [1])

    @patch("executor.surface_executor.search_symbols_by_terms", side_effect=fake_term_search)
    def test_intersects_compatible_conditions_and_skips_future_match_kinds(self, _search):
        plan = SurfacePlanner().plan(
            [
                {
                    "match_kind": "code_element",
                    "code_element_type": "function",
                    "keywords": ["login"],
                },
                {
                    "match_kind": "code_element",
                    "code_element_type": "method",
                    "keywords": ["user"],
                },
                {
                    "match_kind": "code_snippet",
                    "keywords": ["validate token"],
                },
            ]
        ).to_dict()

        executor = self._executor()
        try:
            results = executor.execute(plan)
        finally:
            executor.close()
        self.assertEqual([item["symbol_id"] for item in results], [1])
        self.assertTrue(
            any("code_snippet" in warning for warning in executor.execution_report["warnings"])
        )

    @patch("executor.surface_executor.search_symbols_by_terms", side_effect=fake_term_search)
    def test_call_scope_uses_undirected_call_chain_distance(self, _search):
        self._create_call_graph(source_id=1, target_id=3)
        plan = SurfacePlanner().plan(
            [
                {
                    "match_kind": "code_element",
                    "code_element_type": "method",
                    "keyword_groups": [
                        {"group_id": "k1", "property": "include", "keywords": ["login"]},
                        {"group_id": "k2", "property": "include", "keywords": ["account"]},
                    ],
                    "group_logic": [
                        {
                            "groups": ["k1", "k2"],
                            "graph_scope": ["call"],
                            "default_hop_count": 1,
                        }
                    ],
                }
            ]
        ).to_dict()

        executor = self._executor()
        try:
            results = executor.execute(plan)
        finally:
            executor.close()

        self.assertEqual({item["symbol_id"] for item in results}, {1, 3})
        coverage_1 = executor.execution_report["evidence_by_symbol_id"]["1"]["coverage"]
        coverage_3 = executor.execution_report["evidence_by_symbol_id"]["3"]["coverage"]
        self.assertEqual(coverage_1["surface_0"]["k2"]["distance"], 1)
        self.assertEqual(coverage_3["surface_0"]["k1"]["distance"], 1)
        self.assertEqual(coverage_1["surface_0"]["k2"]["match_type"], "graph_neighbor")
        self.assertEqual(coverage_3["surface_0"]["k1"]["match_type"], "graph_neighbor")

    @patch("executor.surface_executor.search_symbols_by_terms", side_effect=fake_term_search)
    def test_run_surface_search_writes_compatible_results_and_evidence(self, _search):
        plan = SurfacePlanner().plan(
            [
                {
                    "match_kind": "code_element",
                    "code_element_type": "method",
                    "keyword_groups": [
                        {
                            "group_id": "k1",
                            "property": "include",
                            "keywords": ["login"],
                        },
                        {
                            "group_id": "k2",
                            "property": "include",
                            "keywords": ["user"],
                        },
                    ],
                    "group_logic": [
                        {
                            "groups": ["k1", "k2"],
                            "graph_scope": ["call"],
                            "default_hop_count": 0,
                        }
                    ],
                }
            ],
            raw_query="Find user login",
        ).to_dict()
        plan_path = self.query_dir / "surface_semql.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        results = run_surface_search(
            surface_plan_path=str(plan_path),
            output_dir=str(self.query_dir),
            project_output_dir=str(self.project_dir),
        )

        self.assertEqual([item["symbol_id"] for item in results], [1])
        self.assertTrue((self.query_dir / "filtered_by_type_hop_0.json").exists())
        evidence = json.loads((self.query_dir / "surface_evidence_hop_0.json").read_text())
        self.assertEqual(evidence["result_count"], 1)

        direct_results = json.loads(
            (self.query_dir / "surface_group_search_results.json").read_text()
        )
        self.assertEqual(direct_results["stage"], "before_group_expression")
        clause = direct_results["conditions"]["surface_0"]["surface_0_include"]
        self.assertEqual(clause["direct_result_count_by_group"], {"k1": 2, "k2": 2})
        self.assertEqual(
            [item["symbol_id"] for item in clause["groups"]["k1"]["results"]],
            [1, 2],
        )
        self.assertEqual(
            [item["symbol_id"] for item in clause["groups"]["k2"]["results"]],
            [1, 3],
        )
        self.assertEqual(
            clause["groups"]["k1"]["evidence_by_symbol_id"]["1"]["term"],
            "login",
        )

    def _create_call_graph(self, source_id, target_id):
        connection = sqlite3.connect(self.project_dir / "codegraph.sqlite")
        try:
            connection.executescript(
                """
                CREATE TABLE code_symbols (
                  symbol_id INTEGER PRIMARY KEY,
                  name TEXT,
                  file TEXT
                );
                CREATE TABLE code_edges (
                  edge_id INTEGER PRIMARY KEY,
                  source_symbol_id INTEGER,
                  target_symbol_id INTEGER,
                  kind TEXT,
                  source_impl_symbol_id INTEGER,
                  target_impl_symbol_id INTEGER
                );
                CREATE TABLE code_implementations (
                  impl_id INTEGER PRIMARY KEY,
                  abstract_symbol_id INTEGER,
                  implementation_symbol_id INTEGER,
                  abstract_owner_symbol_id INTEGER,
                  implementation_owner_symbol_id INTEGER,
                  relation_kind TEXT,
                  confidence REAL,
                  provenance TEXT,
                  is_ambiguous INTEGER,
                  raw_lsp TEXT
                );
                """
            )
            connection.executemany(
                "INSERT INTO code_symbols(symbol_id, name, file) VALUES (?, ?, ?)",
                [(1, "userLogin", "Auth.java"), (3, "userAccount", "User.java")],
            )
            connection.execute(
                "INSERT INTO code_edges(source_symbol_id, target_symbol_id, kind) VALUES (?, ?, 'calls')",
                (source_id, target_id),
            )
            connection.commit()
        finally:
            connection.close()


class SurfaceSearchCompatibilityTest(unittest.TestCase):
    def test_direct_term_api_keeps_legacy_matcher_result(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            invert_path = Path(tmp_dir) / "invert_index.json"
            ngram_path = Path(tmp_dir) / "ngramed_symbol.json"
            invert_path.write_text(json.dumps({"login": ["login"]}), encoding="utf-8")
            ngram_path.write_text(
                json.dumps({"login": [{"symbol_id": 1, "name": "login", "type": "method"}]}),
                encoding="utf-8",
            )
            matcher = FullTermMatcher(str(invert_path), str(ngram_path))
            legacy_payload = {
                "conditions": {
                    "surface": {
                        "include": [{"keywords": ["login"]}],
                    }
                }
            }

            self.assertEqual(
                matcher.match_ngram(legacy_payload)["matched_subtokens"],
                matcher.match_terms(["login"])["matched_subtokens"],
            )
            result = search_symbols_by_terms(
                str(invert_path),
                str(ngram_path),
                ["login"],
                matcher=matcher,
            )
            self.assertEqual([item["symbol_id"] for item in result["symbols"]], [1])

    def test_type_filter_uses_only_planner_types(self):
        records = [
            {"symbol_id": 1, "type": "method"},
            {"symbol_id": 2, "type": "class"},
        ]
        self.assertEqual(
            filter_symbols_by_type(records, ["function", "method"]),
            [records[0]],
        )


if __name__ == "__main__":
    unittest.main()
