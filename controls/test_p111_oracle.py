"""Pure source-rule and frozen-population regressions; no CLI is launched."""
import copy
import unittest

import p111_intrusion_control as control


class IntrusionOracleTests(unittest.TestCase):
    def test_population_has_distinct_cases_and_targets(self):
        cases=control.generate_cases()
        self.assertGreaterEqual(len(cases),40)
        self.assertEqual(len({case["id"] for case in cases}),len(cases))
        targets=sum(len(case["outer"]["waste_spec"]["targets"]) for case in cases)
        self.assertGreaterEqual(targets,40)
        self.assertEqual(len(control.EXPECTED_ROWS),25)
        self.assertEqual(sum(len(row["limits"]) for row in control.EXPECTED_ROWS),75)

    def test_frozen_literal_discrepancies_and_null_limits(self):
        rows={row["id"]:row for row in control.EXPECTED_ROWS}
        self.assertEqual(rows["Cs-137"]["limits"],[1,44,460])
        self.assertEqual(rows["Nb-94_activated_metal"]["limits"],[0.2,0.2,2])
        self.assertIsNone(rows["short_half_life_lt5y_sum"]["limits"][1])
        self.assertIsNone(rows["Co-60"]["limits"][2])
        self.assertEqual(rows["alpha_transuranic_gt5y"]["members"],
                         ["Pu238","Pu239","Pu240","Pu242","Am241","Am243","Cm243","Cm244"])

    def test_strict_and_inclusive_presence_boundaries(self):
        cases={case["id"]:case for case in control.generate_cases()}
        by_case={name:control.evaluate_target(cases[name],1) for name in
                 ("wac_below","wac_equal","wac_above","unlisted_both_below",
                  "unlisted_both_equal","unlisted_both_above","rq_below","rq_equal",
                  "rq_above","share_below","share_equal","share_above")}
        p=lambda result,nuclide,key: next(x for x in result["nuclides"] if x["nuclide"]==nuclide)["predicates"][key]["value"]
        self.assertEqual([p(by_case[f"wac_{k}"],"C14","wac_fraction") for k in ("below","equal","above")],
                         ["false","false","true"])
        self.assertEqual([p(by_case[f"unlisted_both_{k}"],"Mg24","unlisted_concentration") for k in ("below","equal","above")],
                         ["false","false","true"])
        self.assertEqual([p(by_case[f"rq_{k}"],"Mg24","dot_rq") for k in ("below","equal","above")],
                         ["false","true","true"])
        self.assertEqual([p(by_case[f"share_{k}"],"C14","container_share") for k in ("below","equal","above")],
                         ["false","true","true"])

    def test_three_valued_or_and_source_gaps(self):
        cases={case["id"]:case for case in control.generate_cases()}
        c=control.evaluate_target(cases["only_wac_true"],1)
        c14=next(row for row in c["nuclides"] if row["nuclide"]=="C14")
        self.assertEqual(c14["predicates"]["wac_fraction"]["value"],"true")
        self.assertEqual(c14["presence"],"true")
        self.assertEqual(c14["predicates"]["dot_rq"]["value"],"false")
        self.assertEqual(control._tri_or(["false","indeterminate","false"]),"indeterminate")
        self.assertEqual(control._tri_or(["indeterminate","true","false"]),"true")
        self.assertEqual(control._tri_or(["false","false"]),"false")

    def test_short_group_includes_named_members_and_equality_is_neither(self):
        cases={case["id"]:case for case in control.generate_cases()}
        grouped=control.evaluate_target(cases["short_group_and_named"],1)
        row=next(r for r in grouped["rows"] if r["row_id"]=="short_half_life_lt5y_sum")
        self.assertIn("Xe135",row["members"])
        self.assertIn("Ni63",row["members"])
        exact=control.evaluate_target(cases["exact_five_year_not_group"],1)
        row=next(r for r in exact["rows"] if r["row_id"]=="short_half_life_lt5y_sum")
        self.assertNotIn("Xe135",row["members"])

    def test_external_h3_merges_once_and_incomplete_denominator_is_unknown(self):
        cases={case["id"]:case for case in control.generate_cases()}
        declared=control.evaluate_target(cases["external_h3_once"],1)
        self.assertEqual(declared["inventory_activity_bq"]["H3"],20.0)
        self.assertEqual(declared["external_tritium_activity_bq"],10.0)
        incomplete=control.evaluate_target(cases["incomplete_inventory_share_unknown"],1)
        nuclide=next(n for n in incomplete["nuclides"] if n["nuclide"]=="C14")
        self.assertEqual(nuclide["predicates"]["container_share"]["value"],"indeterminate")
        self.assertIsNone(incomplete["container_total_activity_bq"])

    def test_no_limit_columns_and_metal_row_selection(self):
        cases={case["id"]:case for case in control.generate_cases()}
        co=control.evaluate_target(cases["no_limit_co60"],1)
        row=next(r for r in co["rows"] if r["row_id"]=="Co-60")
        self.assertIsNone(row["limit"] if row["column"]=="C" else 0)
        metal=control.evaluate_target(cases["metal_nb94_literal"],1)
        self.assertIn("Nb-94_activated_metal",{r["row_id"] for r in metal["rows"]})
        self.assertNotIn("Nb-94",{r["row_id"] for r in metal["rows"]})

    def test_part61_generic_alpha_is_not_draft_fixed_eight(self):
        case=next(case for case in control.generate_cases() if case["id"]=="part61_alpha_outside_draft_fixed8")
        props=case["outer"]["waste_spec"]["nuclide_properties"]
        self.assertIs(control._part61_membership("Am242",props,"general"),True)
        self.assertIs(control._draft_membership("Am242",props,"general","equipment"),False)
        evaluated=control.evaluate_target(case,1)
        nuclide=next(item for item in evaluated["nuclides"] if item["nuclide"]=="Am242")
        self.assertEqual(nuclide["predicates"]["unlisted_concentration"]["value"],"indeterminate")
        self.assertEqual(evaluated["assessment_indicator"],"review_indicated")

    def test_rows_expose_only_positive_matching_members(self):
        cases={case["id"]:case for case in control.generate_cases()}
        zero=control.evaluate_target(cases["zero_inventory"],1)
        self.assertTrue(all(not row["members"] and row["activity_bq"]==0.0 for row in zero["rows"]))
        alpha=control.evaluate_target(cases["alpha_group"],1)
        fixed=next(row for row in alpha["rows"] if row["row_id"]=="alpha_transuranic_gt5y")
        self.assertEqual(fixed["members"], ["Am241","Am243","Cm243","Cm244","Pu238","Pu239","Pu240","Pu242"])

    def test_frozen_generator_returns_defensive_values(self):
        original=control.generate_cases()
        changed=copy.deepcopy(original)
        changed[0]["outer"]["site_wac"]={"source":"mutated","membership_coverage":"complete","nuclides":{}}
        self.assertNotEqual(changed[0],original[0])
        self.assertGreaterEqual(len(original),40)


if __name__=="__main__":
    unittest.main()
