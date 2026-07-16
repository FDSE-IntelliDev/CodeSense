import unittest

from query_processing.planners import (
    IntentionPlanner,
    QueryPlanner,
    RelationPlanner,
    SurfacePlanner,
)


class SurfacePlannerTest(unittest.TestCase):
    def test_adapts_legacy_terms_properties_and_types(self):
        plan = SurfacePlanner().plan(
            [
                {
                    "property": "INCLUDE",
                    "keywords": ["login", "auth", "LOGIN"],
                    "synonyms": ["auth", "signIn"],
                    "match_kind": "code_element",
                    "code_element_type": "function",
                },
                {
                    "property": "exclude",
                    "keywords": ["test"],
                    "synonyms": [],
                    "match_kind": "unknown-value",
                    "code_element_type": ["class"],
                },
            ],
            raw_query="Find login code",
        ).to_dict()

        self.assertEqual(plan["kind"], "surface")
        self.assertNotIn("relation", plan)
        self.assertNotIn("intention", plan)
        include_condition = plan["conditions"][0]
        include = include_condition["include_clause"]
        self.assertEqual(include["clause_id"], "surface_0_include")
        self.assertEqual(
            include["keyword_groups"][0]["term_expression"]["terms"],
            [
                {"value": "login", "source": "keyword"},
                {"value": "auth", "source": "keyword"},
                {"value": "signIn", "source": "synonym"},
            ],
        )
        self.assertEqual(
            include_condition["match"]["code_element_types"],
            ["function", "method"],
        )
        self.assertEqual(include["group_expression"]["operator"], "identity")

        exclude_condition = plan["conditions"][1]
        self.assertIsNone(exclude_condition["include_clause"])
        self.assertEqual(
            exclude_condition["exclude_clause"]["clause_id"],
            "surface_1_exclude",
        )
        self.assertEqual(exclude_condition["match"]["kind"], "unknown")
        self.assertEqual(
            exclude_condition["result_expression"]["operator"],
            "exclude_only",
        )
        self.assertEqual(
            plan["condition_expression"]["exclude_only_condition_ids"],
            ["surface_1"],
        )

    def test_invalid_property_defaults_to_include(self):
        plan = SurfacePlanner().plan(
            [{"property": "other", "keywords": "cache"}]
        )
        self.assertIsNotNone(plan.conditions[0].include_clause)
        self.assertIsNone(plan.conditions[0].exclude_clause)

    def test_parses_keyword_groups_and_group_logic(self):
        plan = SurfacePlanner().plan(
            [
                {
                    "type": "surface",
                    "match_kind": "code_element",
                    "code_element_type": "function",
                    "code_text": None,
                    "keyword_groups": [
                        {
                            "group_id": "k1",
                            "property": "include",
                            "keywords": ["login"],
                            "synonyms": ["auth", "authenticate", "sign_in", "log_in"],
                            "reason": "Core authentication action",
                        },
                        {
                            "group_id": "k2",
                            "property": "include",
                            "keywords": ["user"],
                            "synonyms": ["account", "credential", "identity", "principal"],
                            "reason": "Authenticated entity",
                        },
                    ],
                    "group_logic": [
                        {
                            "groups": ["k1", "k2"],
                            "graph_scope": ["call"],
                            "default_hop_count": 2,
                            "pairwise_hop_counts": [
                                {
                                    "groups": ["k1", "k2"],
                                    "hop_count": 0,
                                    "reason": "Both concepts should be co-located",
                                }
                            ],
                            "reason": "Joint login-user concept",
                        }
                    ],
                }
            ],
            raw_query="Find the user login entry point",
        ).to_dict()

        clause = plan["conditions"][0]["include_clause"]
        self.assertEqual(
            clause["keyword_groups"][0]["term_expression"]["terms"],
            [
                {"value": "login", "source": "keyword"},
                {"value": "auth", "source": "synonym"},
                {"value": "authenticate", "source": "synonym"},
                {"value": "sign_in", "source": "synonym"},
                {"value": "log_in", "source": "synonym"},
            ],
        )
        self.assertEqual(
            clause["keyword_groups"][1]["term_expression"]["terms"],
            [
                {"value": "user", "source": "keyword"},
                {"value": "account", "source": "synonym"},
                {"value": "credential", "source": "synonym"},
                {"value": "identity", "source": "synonym"},
                {"value": "principal", "source": "synonym"},
            ],
        )
        self.assertEqual(
            [group["group_id"] for group in clause["keyword_groups"]],
            ["k1", "k2"],
        )
        self.assertEqual(clause["keyword_groups"][0]["property"], "include")
        self.assertEqual(clause["keyword_groups"][0]["term_expression"]["operator"], "or")
        self.assertEqual(clause["group_expression"]["operator"], "and_hop")
        self.assertEqual(clause["group_expression"]["groups"], ["k1", "k2"])
        rule = clause["group_expression"]["rules"][0]
        self.assertEqual(rule["groups"], ["k1", "k2"])
        self.assertEqual(rule["graph_scope"], ["call"])
        self.assertEqual(rule["default_hop_count"], 2)
        self.assertEqual(
            rule["pairwise_hop_counts"][0]["hop_count"],
            0,
        )

    def test_splits_mixed_group_properties_and_subtracts_exclude_clause(self):
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
                        {
                            "group_id": "k3",
                            "property": "exclude",
                            "keywords": ["logout"],
                            "synonyms": ["signout"],
                        },
                    ],
                    "group_logic": [
                        {
                            "groups": ["k1", "k2", "k3"],
                            "graph_scope": ["call"],
                            "default_hop_count": 1,
                        }
                    ],
                }
            ]
        ).to_dict()

        condition = plan["conditions"][0]
        self.assertEqual(
            [group["group_id"] for group in condition["include_clause"]["keyword_groups"]],
            ["k1", "k2"],
        )
        self.assertEqual(
            condition["include_clause"]["group_expression"]["operator"],
            "and_hop",
        )
        self.assertEqual(
            condition["include_clause"]["group_expression"]["rules"][0]["groups"],
            ["k1", "k2"],
        )
        self.assertEqual(
            [group["group_id"] for group in condition["exclude_clause"]["keyword_groups"]],
            ["k3"],
        )
        self.assertEqual(
            condition["exclude_clause"]["group_expression"]["operator"],
            "identity",
        )
        self.assertEqual(condition["result_expression"]["operator"], "subtract")

    def test_merges_compatible_conditions_by_intersection_and_others_by_union(self):
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
                {
                    "match_kind": "code_element",
                    "code_element_type": "class",
                    "keywords": ["AuthService"],
                },
            ]
        ).to_dict()

        expression = plan["condition_expression"]
        self.assertEqual(expression["operator"], "union")
        self.assertEqual(
            expression["groups"],
            [
                {
                    "operator": "intersect",
                    "condition_ids": ["surface_0", "surface_1"],
                    "match_kind": "code_element",
                    "code_element_types": ["function", "method"],
                },
                {
                    "operator": "identity",
                    "condition_ids": ["surface_2"],
                    "match_kind": "code_snippet",
                    "code_element_types": [],
                },
                {
                    "operator": "identity",
                    "condition_ids": ["surface_3"],
                    "match_kind": "code_element",
                    "code_element_types": ["class"],
                },
            ],
        )

    def test_all_exclude_keyword_groups_route_clause_to_exclude(self):
        plan = SurfacePlanner().plan(
            [
                {
                    "keyword_groups": [
                        {
                            "group_id": "k1",
                            "property": "exclude",
                            "keywords": ["test"],
                            "synonyms": ["mock"],
                        }
                    ]
                }
            ]
        )

        condition = plan.conditions[0]
        self.assertIsNone(condition.include_clause)
        self.assertIsNotNone(condition.exclude_clause)
        self.assertEqual(
            [
                term.value
                for term in condition.exclude_clause.keyword_groups[0].term_expression.terms
            ],
            ["test", "mock"],
        )


class RelationPlannerTest(unittest.TestCase):
    def test_parses_call_anchors_and_normalizes_roles(self):
        plan = RelationPlanner().plan(
            [
                {
                    "property": "include",
                    "file_path": "src/auth",
                    "container": "AuthService",
                    "code_element_type": "method",
                    "graph_constraint": {
                        "role": "entrypoint",
                        "relation": "distance_leq",
                        "value": 0,
                    },
                    "caller": "AuthController.java:login:2",
                    "callee": "None:verifyToken:1",
                    "code_ql": "select callable",
                    "description": "Login relation",
                },
                {
                    "property": "exclude",
                    "graph_constraint": {"role": "isolated"},
                    "caller": "testLogin",
                },
            ],
            raw_query="Find login flow",
        ).to_dict()

        self.assertEqual(plan["kind"], "relation")
        self.assertNotIn("surface", plan)
        include = plan["filters"]["include"][0]
        self.assertEqual(include["graph_constraint"]["role"], "entry_point")
        self.assertEqual(
            include["caller"],
            {
                "file_name": "AuthController.java",
                "symbol_name": "login",
                "hop_count": 2,
            },
        )
        self.assertEqual(
            include["callee"],
            {
                "file_name": None,
                "symbol_name": "verifyToken",
                "hop_count": 1,
            },
        )
        exclude = plan["filters"]["exclude"][0]
        self.assertEqual(exclude["graph_constraint"]["role"], "isolate")
        self.assertEqual(exclude["caller"]["symbol_name"], "testLogin")

    def test_invalid_or_negative_hop_is_ignored(self):
        self.assertIsNone(RelationPlanner.parse_call_anchor("None:login:-1").hop_count)
        self.assertIsNone(RelationPlanner.parse_call_anchor("None:login:x").hop_count)


class IntentionPlannerTest(unittest.TestCase):
    def test_builds_profile_from_include_requirements_only(self):
        plan = IntentionPlanner().plan(
            [
                {
                    "property": "include",
                    "intent": {"action": "authenticate", "object": "user"},
                    "intent_statement": "Does it authenticate a user?",
                    "aspect": "FUNCTIONAL",
                    "non_functional_type": "null",
                    "keywords": ["login", "credential validation", "LOGIN"],
                    "description": "Authentication responsibility",
                },
                {
                    "property": "exclude",
                    "intent": {"action": "logout", "object": "user"},
                    "keywords": ["logout"],
                },
            ],
            raw_query="Find login handler",
        ).to_dict()

        self.assertEqual(plan["kind"], "intention")
        self.assertNotIn("surface", plan)
        self.assertNotIn("relation", plan)
        self.assertEqual(
            plan["query_profile"]["terms"],
            ["authenticate", "user", "login", "credential validation"],
        )
        self.assertEqual(
            plan["query_profile"]["semantic_text"],
            "Find login handler authenticate user login credential validation",
        )
        include = plan["requirements"]["include"][0]
        self.assertEqual(include["aspect"], "functional")
        self.assertIsNone(include["non_functional_type"])
        self.assertEqual(len(plan["requirements"]["exclude"]), 1)


class QueryPlannerTest(unittest.TestCase):
    def test_routes_conditions_to_independent_plans(self):
        bundle = QueryPlanner().plan(
            {
                "surface": [{"keywords": ["login"]}],
                "relation": [{"caller": "login"}],
                "intention": [
                    {"intent": {"action": "authenticate", "object": "user"}}
                ],
            },
            raw_query="Find login handler",
        )

        self.assertEqual(bundle.surface.kind, "surface")
        self.assertEqual(bundle.relation.kind, "relation")
        self.assertEqual(bundle.intention.kind, "intention")
        self.assertEqual(
            bundle.manifest(raw_query="Find login handler"),
            {
                "version": "1.0",
                "raw_query": "Find login handler",
                "plans": {
                    "surface": "surface_semql.json",
                    "relation": "relation_semql.json",
                    "intention": "intention_semql.json",
                },
            },
        )

    def test_non_dict_semcon_produces_empty_plans(self):
        bundle = QueryPlanner().plan(None, raw_query="query")
        self.assertEqual(bundle.surface.conditions, [])
        self.assertEqual(bundle.surface.condition_expression.groups, [])
        self.assertEqual(bundle.surface.condition_expression.operator, "empty")
        self.assertEqual(bundle.relation.filters.include, [])
        self.assertEqual(bundle.intention.requirements.include, [])


if __name__ == "__main__":
    unittest.main()
