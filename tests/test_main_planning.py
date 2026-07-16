import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import process_online


class ProcessOnlinePlanningTest(unittest.TestCase):
    @patch("main.run_intention_executor", return_value=[])
    @patch("main.run_relation_executor", return_value=[])
    @patch("main.run_surface_search", return_value=[])
    def test_writes_domain_plans_and_passes_surface_plan_to_executor(
        self,
        _surface_executor,
        _relation_executor,
        _intention_executor,
    ):
        semcon = {
            "surface": [
                {
                    "property": "include",
                    "keywords": ["login"],
                    "synonyms": ["signin"],
                    "match_kind": "code_element",
                    "code_element_type": "function",
                }
            ],
            "relation": [
                {
                    "property": "include",
                    "caller": "AuthController.java:login:1",
                }
            ],
            "intention": [
                {
                    "property": "include",
                    "intent": {"action": "authenticate", "object": "user"},
                    "keywords": ["login"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            query_dir = Path(tmp_dir) / "query_7"
            query_dir.mkdir(parents=True)
            (query_dir / "semCon.json").write_text(
                json.dumps(semcon, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result = process_online(
                "Find login handler",
                output_dir=tmp_dir,
                query_id=7,
            )

            expected_files = {
                "semCon.json",
                "query_plan.json",
                "surface_semql.json",
                "relation_semql.json",
                "intention_semql.json",
            }
            self.assertTrue(expected_files.issubset({p.name for p in query_dir.iterdir()}))

            surface = json.loads(
                (query_dir / "surface_semql.json").read_text(encoding="utf-8")
            )
            relation = json.loads(
                (query_dir / "relation_semql.json").read_text(encoding="utf-8")
            )
            intention = json.loads(
                (query_dir / "intention_semql.json").read_text(encoding="utf-8")
            )
            manifest = json.loads(
                (query_dir / "query_plan.json").read_text(encoding="utf-8")
            )

            self.assertEqual(surface["kind"], "surface")
            self.assertNotIn("relation", surface)
            self.assertEqual(relation["kind"], "relation")
            self.assertNotIn("intention", relation)
            self.assertEqual(intention["kind"], "intention")
            self.assertNotIn("surface", intention)
            self.assertEqual(
                manifest["plans"],
                {
                    "surface": "surface_semql.json",
                    "relation": "relation_semql.json",
                    "intention": "intention_semql.json",
                },
            )
            self.assertEqual(result["query_plan_path"], str(query_dir / "query_plan.json"))
            self.assertEqual(result["surface_evidence_path"], str(query_dir / "surface_evidence_hop_0.json"))
            self.assertEqual(
                result["surface_group_search_result_path"],
                str(query_dir / "surface_group_search_results.json"),
            )
            _surface_executor.assert_called_once_with(
                surface_plan_path=str(query_dir / "surface_semql.json"),
                output_dir=str(query_dir),
                project_output_dir=tmp_dir,
            )


if __name__ == "__main__":
    unittest.main()
