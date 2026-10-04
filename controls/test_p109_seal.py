"""Small seal and verdict regressions; these never launch ACTINV."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p109
import check_p109_verdict
import p109_composition_solve_control as oracle


class P109SealTests(unittest.TestCase):
    def test_g0_persisted_seal_replays_without_diagnostic_drift(self):
        report={"schema":"actinv-p109-composition-solve-g0-1","phase":"P109",
                "pass":True,"case_count":9,"expected_labels":{"pass":True}}
        with tempfile.TemporaryDirectory(prefix="p109-seal-") as temp:
            seal=Path(temp)/"g0.json"
            with patch.object(check_p109,"_g0_base",side_effect=lambda:copy.deepcopy(report)), \
                 patch.object(check_p109,"G0",seal), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check_p109.g0(seal=True),0)
                self.assertEqual(check_p109.g0(no_write=True),0)
                self.assertEqual(check_p109.g0(no_write=True),0)
                mutated=json.loads(seal.read_text())
                mutated["case_count"]=8
                seal.write_text(json.dumps(mutated))
                self.assertEqual(check_p109.g0(no_write=True),1)

    def test_decimal_capture_reference_is_positive_and_decays(self):
        a1 = oracle.analytic_nb_activity(oracle.Decimal("1"), 1)
        a2 = oracle.analytic_nb_activity(oracle.Decimal("1"), 2)
        self.assertGreater(a1, 0)
        self.assertGreater(a2, 0)
        self.assertLess(a2, a1)

    def test_p109_source_labels_cover_volume_and_h3_boundaries(self):
        labels=check_p109._expected_labels(oracle.generate_cases())
        by_id={row["id"]:(row["lower_class"],row["upper_class"]) for row in labels["labels"]}
        self.assertTrue(labels["pass"])
        self.assertEqual(by_id["high_fixed_volume_7"],("A","A"))
        self.assertEqual(by_id["ranged_volume_point_1"],("C","above_class_c"))
        self.assertEqual(by_id["low_fixed_external_h3"],("A","B"))

    def test_zero_response_has_canonical_feasible_reference(self):
        case=oracle.generate_cases()[0]
        target=[{"step":1,"element_activity_bq_per_g":{"Fe":{},"Nb":{},"Si":{}}}]
        witnesses=oracle._range_witnesses(case,target)
        canonical=check_p109.p108.greedy_expected(case["composition_wt_percent_bounds"],
                                                    {"Fe":0.0,"Nb":0.0,"Si":0.0},False)
        key=tuple((n,float(v)) for n,v in sorted(canonical.items()))
        self.assertEqual(witnesses[key],[{"kind":"canonical_reference"}])

    def test_missing_nb94_decay_means_capture_leakage_not_analytic_decay(self):
        composition={"Nb":oracle.Decimal("1"),"Fe":oracle.Decimal("50"),"Si":oracle.Decimal("49")}
        activity,atoms=oracle.expected_inventory({"variant":"omit_nb94_decay"},composition,2)
        with oracle.localcontext() as ctx:
            ctx.prec=80
            initial=oracle.AVOGADRO/oracle.NB_MOLAR_MASS/100
            retained=initial*(-oracle.CAPTURE_RATE*oracle.IRRADIATION_S).exp()
        self.assertEqual(activity,{})
        self.assertNotIn("Nb94",atoms)
        self.assertNotIn("Mo94",atoms)
        self.assertLess(atoms["Nb93"],initial)
        self.assertLess(abs(atoms["Nb93"]-retained),oracle.Decimal("1e-70")*initial)
        self.assertGreater(initial-atoms["Nb93"],0)  # independently derived capture leakage

    def test_atom_inventory_uses_vector_l1_and_stable_isotope_controls(self):
        expected={"Fe56":oracle.Decimal("1e20"),"Nb93":oracle.Decimal("1e10"),
                  "Mo94":oracle.Decimal("1e8")}
        small={k:float(v) for k,v in expected.items()}; small["Mo94"]+=1e5
        self.assertEqual(oracle._atoms_close(small,expected),[])
        bad=dict(small); bad["Nb93"]+=1e8
        self.assertTrue(oracle._atoms_close(bad,expected))

    def test_fixture_contract_rejects_population_mutations(self):
        cases=oracle.generate_cases()
        fixture={"schema":oracle.CASE_SCHEMA,
                 "source":"artificial native Fe/Si/Nb capture and decay model; no evaluated nuclear data",
                 "cases":cases}
        generated={"complete":{"target_count":10,"row_count":11,"decay_record_count":12},
                   "omit_fe58_target":{"target_count":9,"row_count":10,"decay_record_count":12},
                   "omit_nb94_decay":{"target_count":10,"row_count":11,"decay_record_count":11}}
        self.assertTrue(check_p109._fixture_contract(cases,fixture,generated)["pass"])
        for mutation in ("weight","h3","target_order"):
            changed=copy.deepcopy(cases)
            if mutation=="weight": changed[0]["composition_wt_percent_bounds"]["Nb"]["upper_wt_percent"]=9.0
            elif mutation=="h3": changed[3]["external_tritium"]["activity_bounds_bq"]["1"]["upper_bq"]=1.0
            else: changed[0]["targets"]=[2,1]
            self.assertFalse(check_p109._fixture_contract(changed,fixture,generated)["pass"])

    def test_quality_rejects_boolean_counters_and_missing_sources(self):
        sources = {path: check_p109_verdict.sha(check_p109_verdict.ROOT / path)
                   for path in check_p109_verdict.REQUIRED_SOURCES}
        base = {"schema":"actinv-p109-quality-1","phase":"P109","pass":True,
                "gates":{name:{"exit_code":0} for name in check_p109_verdict.REQUIRED_GATES},
                "resource_limits":check_p109_verdict.RESOURCES,
                "workspace_tests":{"passed":1,"failed":0},"production_rust_sha256":sources}
        self.assertTrue(check_p109_verdict._quality(base,None)[0])
        for field, value in (("exit_code",False),):
            bad=copy.deepcopy(base)
            bad["gates"][next(iter(check_p109_verdict.REQUIRED_GATES))][field]=value
            self.assertFalse(check_p109_verdict._quality(bad,None)[0])
        for field,value in (("passed",True),("failed",False)):
            bad=copy.deepcopy(base); bad["workspace_tests"][field]=value
            self.assertFalse(check_p109_verdict._quality(bad,None)[0])
        bad=copy.deepcopy(base)
        bad["production_rust_sha256"].pop("crates/actinv-core/src/run.rs")
        self.assertFalse(check_p109_verdict._quality(bad,None)[0])


if __name__ == "__main__":
    unittest.main()
