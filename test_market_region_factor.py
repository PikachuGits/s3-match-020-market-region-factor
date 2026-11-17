from __future__ import annotations

import math
import unittest

from market_region_factor import (
    PARENT_CHILD_MAX_DEPTH,
    UNRELATED_PAIR_SCORE,
    RegionDataError,
    RegionHierarchy,
    RegionRecord,
    UnknownRegionCodeError,
    calculate_market_region_factor,
)


class MarketRegionFactorTests(unittest.TestCase):
    def setUp(self) -> None:
        # Synthetic fixture codes only; these are not customer or production data.
        self.hierarchy = RegionHierarchy(
            [
                RegionRecord("R-NATIONAL", None, True),
                RegionRecord("R-SOUTH", "R-NATIONAL", True),
                RegionRecord("R-GD", "R-SOUTH", True),
                RegionRecord("R-GX", "R-SOUTH", True),
                RegionRecord("R-EAST", "R-NATIONAL", True),
                RegionRecord("R-OVERSEAS", None, False),
                RegionRecord("R-OTHER-ROOT", None, False),
                RegionRecord("R-SEA", "R-OVERSEAS", False),
                RegionRecord("R-VN", "R-SEA", False),
                RegionRecord("R-TH", "R-SEA", False),
            ]
        )

    def score(self, demand, supply=None, capability=None):
        return calculate_market_region_factor(
            self.hierarchy,
            demand_market_codes=demand,
            supply_market_codes=supply,
            capability_service_area_tags=capability,
            national_code="R-NATIONAL",
        )

    def test_exact_match_scores_one(self) -> None:
        result = self.score(["R-GD"], supply=["R-GD"])
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs[0].relation, "exact")

    def test_exact_national_match_precedes_national_override(self) -> None:
        result = self.score(["R-NATIONAL"], supply=["R-NATIONAL"])
        self.assertEqual(result.score, 1.0)

    def test_direct_parent_child_scores_point_seven_in_both_directions(self) -> None:
        child_demand = self.score(["R-GD"], supply=["R-SOUTH"])
        parent_demand = self.score(["R-SOUTH"], supply=["R-GD"])
        self.assertEqual(child_demand.score, 0.7)
        self.assertEqual(parent_demand.score, 0.7)

    def test_direct_siblings_score_point_three(self) -> None:
        result = self.score(["R-GD"], supply=["R-GX"])
        self.assertEqual(result.score, 0.3)

    def test_national_special_overrides_direct_parent_child(self) -> None:
        # R-SOUTH is the direct child of R-NATIONAL; special case still wins.
        result = self.score(["R-SOUTH"], supply=["R-NATIONAL"])
        self.assertEqual(result.score, 0.6)
        self.assertEqual(
            result.evidence.demand_results[0].matched_pairs[0].relation,
            "national_domestic_special",
        )

    def test_national_special_applies_to_deeper_domestic_descendant(self) -> None:
        # Original task says any domestic region, so the national rule also
        # covers R-GD two levels below R-NATIONAL.
        result = self.score(["R-GD"], supply=["R-NATIONAL"])
        self.assertEqual(result.score, 0.6)

    def test_national_candidate_does_not_get_special_score_for_non_domestic(self) -> None:
        result = self.score(["R-VN"], supply=["R-NATIONAL"])
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs, ())

    def test_unrelated_valid_regions_use_explicit_zero_assumption(self) -> None:
        result = self.score(["R-GD"], supply=["R-VN"])
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.evidence.demand_results[0].score, 0.0)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs, ())

    def test_non_direct_ancestor_uses_zero_assumption(self) -> None:
        # R-OVERSEAS is a grandparent of R-VN, not a direct parent.
        result = self.score(["R-VN"], supply=["R-OVERSEAS"])
        self.assertEqual(result.score, 0.0)

    def test_boundary_defaults_are_declared(self) -> None:
        self.assertEqual(UNRELATED_PAIR_SCORE, 0.0)
        self.assertEqual(PARENT_CHILD_MAX_DEPTH, 1)

    def test_parent_child_depth_boundary_is_direct_edge_only(self) -> None:
        self.assertEqual(self.hierarchy.parent_child_depth("R-SOUTH", "R-GD"), 1)
        self.assertEqual(self.hierarchy.parent_child_depth("R-OVERSEAS", "R-VN"), 2)
        result = self.score(["R-VN"], supply=["R-OVERSEAS"])
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs, ())

    def test_duplicate_demand_codes_are_deduplicated(self) -> None:
        result = self.score(["R-GD", "R-GD"], supply=["R-SOUTH"])
        self.assertEqual(result.score, 0.7)
        self.assertEqual(len(result.evidence.demand_results), 1)

    def test_root_regions_are_not_siblings_without_a_parent(self) -> None:
        result = self.score(["R-OTHER-ROOT"], supply=["R-EAST"])
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs, ())

    def test_unmatched_demand_region_contributes_zero_to_average(self) -> None:
        result = self.score(
            ["R-GD", "R-OTHER-ROOT"],
            supply=["R-SOUTH"],
        )
        self.assertTrue(math.isclose(result.score, 0.35, rel_tol=0.0, abs_tol=1e-12))
        self.assertEqual([item.score for item in result.evidence.demand_results], [0.7, 0.0])
        self.assertEqual(result.evidence.demand_results[1].matched_pairs, ())

    def test_two_candidate_sources_are_unioned_and_best_is_per_demand(self) -> None:
        result = self.score(
            ["R-GD", "R-EAST", "R-VN"],
            supply=["R-SOUTH", "R-TH"],
            capability=["R-NATIONAL"],
        )

        expected_average = (0.7 + 0.6 + 0.3) / 3
        self.assertTrue(
            math.isclose(result.score, expected_average, rel_tol=0.0, abs_tol=1e-12)
        )
        self.assertEqual(
            [item.demand_code for item in result.evidence.demand_results],
            ["R-GD", "R-EAST", "R-VN"],
        )
        self.assertEqual([item.score for item in result.evidence.demand_results], [0.7, 0.6, 0.3])

        gd_match = result.evidence.demand_results[0].matched_pairs
        east_match = result.evidence.demand_results[1].matched_pairs
        vn_match = result.evidence.demand_results[2].matched_pairs
        self.assertEqual([(m.demand_code, m.candidate_code) for m in gd_match], [("R-GD", "R-SOUTH")])
        self.assertEqual(gd_match[0].candidate_source_field, "supply_item.market_codes")
        self.assertEqual([(m.demand_code, m.candidate_code) for m in east_match], [("R-EAST", "R-NATIONAL")])
        self.assertEqual(
            east_match[0].candidate_source_field,
            "enterprise_capability_card.service_area_tags",
        )
        self.assertEqual([(m.demand_code, m.candidate_code) for m in vn_match], [("R-VN", "R-TH")])
        self.assertEqual(vn_match[0].candidate_source_field, "supply_item.market_codes")

    def test_tied_best_pairs_are_all_retained_in_stable_order(self) -> None:
        result = self.score(["R-SOUTH"], supply=["R-GX", "R-GD"])
        pairs = result.evidence.demand_results[0].matched_pairs
        self.assertEqual(result.score, 0.7)
        self.assertEqual([pair.candidate_code for pair in pairs], ["R-GD", "R-GX"])

    def test_same_candidate_from_two_sources_scores_once_but_keeps_both_sources(self) -> None:
        result = self.score(
            ["R-SOUTH"],
            supply=["R-NATIONAL"],
            capability=["R-NATIONAL"],
        )
        self.assertEqual(result.score, 0.6)
        pairs = result.evidence.demand_results[0].matched_pairs
        self.assertEqual(len(pairs), 2)
        self.assertEqual(
            {pair.candidate_source_field for pair in pairs},
            {
                "supply_item.market_codes",
                "enterprise_capability_card.service_area_tags",
            },
        )

    def test_empty_demand_side_is_neutral_without_reason_but_with_evidence(self) -> None:
        result = self.score(None, supply=["R-SOUTH"])
        self.assertEqual(result.score, 0.5)
        self.assertIsNone(result.reason)
        self.assertIsNotNone(result.evidence)
        self.assertEqual(result.evidence.missing_fields, ("demand.market_codes",))
        self.assertEqual(result.evidence.demand_results, ())

    def test_empty_candidate_side_is_neutral_without_reason_but_with_evidence(self) -> None:
        result = self.score(["R-GD"])
        self.assertEqual(result.score, 0.5)
        self.assertIsNone(result.reason)
        self.assertIsNotNone(result.evidence)
        self.assertEqual(
            result.evidence.missing_fields,
            (
                "supply_item.market_codes",
                "enterprise_capability_card.service_area_tags",
            ),
        )
        self.assertEqual(len(result.evidence.demand_results), 1)
        self.assertEqual(result.evidence.demand_results[0].score, 0.5)
        self.assertEqual(result.evidence.demand_results[0].matched_pairs, ())

    def test_blank_items_are_ignored_when_other_codes_are_present(self) -> None:
        result = self.score(
            [None, " R-GD ", "  "],
            supply=[None, "R-SOUTH", ""],
        )
        self.assertEqual(result.score, 0.7)
        self.assertEqual(result.evidence.missing_fields, ())

    def test_all_blank_items_count_as_a_missing_logical_side(self) -> None:
        result = self.score([None, "  "], supply=[None, ""])
        self.assertEqual(result.score, 0.5)
        self.assertIsNone(result.reason)
        self.assertEqual(result.evidence.demand_results, ())

    def test_unknown_nonblank_code_is_a_data_error_not_missing_or_zero(self) -> None:
        with self.assertRaises(UnknownRegionCodeError):
            self.score(["R-DOES-NOT-EXIST"], supply=["R-SOUTH"])

    def test_non_string_region_code_is_rejected(self) -> None:
        with self.assertRaises(RegionDataError):
            self.score([123], supply=["R-SOUTH"])

    def test_non_iterable_region_codes_are_rejected(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "iterable"):
            self.score(17, supply=["R-SOUTH"])

    def test_hierarchy_rejects_duplicate_codes(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "duplicate"):
            RegionHierarchy(
                [
                    RegionRecord("R-ROOT", None, True),
                    RegionRecord("R-ROOT", None, True),
                ]
            )

    def test_hierarchy_rejects_missing_parent(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "missing parent"):
            RegionHierarchy([RegionRecord("R-CHILD", "R-MISSING", True)])

    def test_hierarchy_rejects_cycles(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "cycle"):
            RegionHierarchy(
                [
                    RegionRecord("R-A", "R-B", True),
                    RegionRecord("R-B", "R-A", True),
                ]
            )

    def test_hierarchy_rejects_non_string_parent_code(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "parent code"):
            RegionHierarchy(
                [
                    RegionRecord("R-ROOT", None, True),
                    RegionRecord("R-CHILD", 3, True),  # type: ignore[arg-type]
                ]
            )

    def test_unknown_candidate_code_is_a_data_error(self) -> None:
        with self.assertRaises(UnknownRegionCodeError):
            self.score(["R-GD"], supply=["R-NOT-IN-DICTIONARY"])

    def test_source_field_names_must_be_nonempty_strings(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "demand source field"):
            calculate_market_region_factor(
                self.hierarchy,
                demand_market_codes=["R-GD"],
                supply_market_codes=["R-SOUTH"],
                national_code="R-NATIONAL",
                demand_source_field=17,  # type: ignore[arg-type]
            )

    def test_national_code_must_be_a_nonempty_string(self) -> None:
        with self.assertRaisesRegex(RegionDataError, "national_code"):
            calculate_market_region_factor(
                self.hierarchy,
                demand_market_codes=["R-GD"],
                supply_market_codes=["R-SOUTH"],
                national_code=17,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
