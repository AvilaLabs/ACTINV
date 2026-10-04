"""P112 seal round-trip regressions; source-only, no subprocesses."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p112


class SealRoundTripTests(unittest.TestCase):
    def test_real_write_read_replay_then_tamper_rejected(self):
        base={"schema":"actinv-p112-intrusion-screen-g0-1","pass":True,
              "phase":"P112","case_count":61,"target_count":62,
              "control_sha256":{"x":"a"*64}}
        with tempfile.TemporaryDirectory() as temp:
            seal=Path(temp)/"g0.json"
            with patch.object(check_p112,"G0",seal), patch.object(check_p112,"_g0_base",side_effect=lambda: copy.deepcopy(base)):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check_p112.g0(seal=True),0)
                    self.assertEqual(check_p112.g0(no_write=True),0)
                    self.assertEqual(check_p112.g0(no_write=True),0)
                    changed=json.loads(seal.read_text(encoding="utf-8"))
                    changed["case_count"]+=1
                    seal.write_text(json.dumps(changed),encoding="utf-8")
                    self.assertEqual(check_p112.g0(no_write=True),1)


    def _valid_p108_g0(self):
        paths={"controls/check_p108_verdict.py","controls/check_p107_history.py",
               "controls/check_p108.py","controls/p108_composition_control.py",
               "controls/test_p107_history.py","controls/test_p108_seal.py"}
        return {"control_hashes":{name:check_p112.sha(check_p112.ROOT/name) for name in paths}}

    def test_p108_control_hash_guard_requires_complete_safe_current_sources(self):
        good=self._valid_p108_g0()
        self.assertTrue(check_p112._p108_sealed_controls_match(good))
        missing=copy.deepcopy(good)
        missing["control_hashes"].pop("controls/check_p107_history.py")
        self.assertFalse(check_p112._p108_sealed_controls_match(missing))
        missing_checker=copy.deepcopy(good)
        missing_checker["control_hashes"].pop("controls/check_p108.py")
        self.assertFalse(check_p112._p108_sealed_controls_match(missing_checker))
        unsafe=copy.deepcopy(good)
        unsafe["control_hashes"]["../controls/check_p107_history.py"]="a"*64
        self.assertFalse(check_p112._p108_sealed_controls_match(unsafe))
        changed=copy.deepcopy(good)
        changed["control_hashes"]["controls/check_p108_verdict.py"]="0"*64
        self.assertFalse(check_p112._p108_sealed_controls_match(changed))

    def test_p108_hash_guard_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"root"; outside=Path(temp)/"outside"
            (root/"controls").mkdir(parents=True); outside.mkdir()
            required=set(check_p112.P108_REQUIRED_CONTROLS)
            for name in required:
                destination=root/name
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_text("sealed source\n",encoding="utf-8")
            external=outside/"external.py"
            external.write_text("outside source\n",encoding="utf-8")
            escaped=root/"controls/check_p108_verdict.py"
            escaped.unlink(); escaped.symlink_to(external)
            hashes={name:check_p112.sha(root/name) for name in required}
            with patch.object(check_p112,"ROOT",root):
                self.assertFalse(check_p112._p108_sealed_controls_match({"control_hashes":hashes}))

    def test_historical_p108_accepts_only_matching_pass(self):
        verdict={"schema":"actinv-p108-verdict-1","phase":"P108","verdict":"P108-PASS"}
        module=types.ModuleType("check_p108_verdict")
        module.derive=lambda: copy.deepcopy(verdict)
        g0=self._valid_p108_g0()
        def read(path):
            return copy.deepcopy(g0 if path.name=="g0_p108_composition.json" else verdict)
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=read):
            self.assertTrue(check_p112._historical_p108())

    def test_historical_p108_rejects_unavailable_verifier(self):
        with patch.dict(sys.modules,{"check_p108_verdict":None}):
            self.assertFalse(check_p112._historical_p108())

    def test_historical_p108_rejects_missing_or_unequal_persisted_evidence(self):
        verdict={"schema":"actinv-p108-verdict-1","phase":"P108","verdict":"P108-PASS"}
        module=types.ModuleType("check_p108_verdict")
        module.derive=lambda: copy.deepcopy(verdict)
        g0=self._valid_p108_g0()
        def missing(path):
            return copy.deepcopy(g0) if path.name=="g0_p108_composition.json" else None
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=missing):
            self.assertFalse(check_p112._historical_p108())
        unequal={**verdict,"evidence_sha256":{"g0":"0"*64}}
        def unequal_read(path):
            return copy.deepcopy(g0) if path.name=="g0_p108_composition.json" else copy.deepcopy(unequal)
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=unequal_read):
            self.assertFalse(check_p112._historical_p108())
        module.derive=lambda: None
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=lambda path: copy.deepcopy(g0 if path.name=="g0_p108_composition.json" else verdict)):
            self.assertFalse(check_p112._historical_p108())

    def test_historical_p108_rejects_nonpass_and_derivation_error(self):
        persisted={"schema":"actinv-p108-verdict-1","phase":"P108","verdict":"P108-PASS"}
        module=types.ModuleType("check_p108_verdict")
        module.derive=lambda: {**persisted,"verdict":"P108-FAIL"}
        g0=self._valid_p108_g0()
        def read(path):
            return copy.deepcopy(g0 if path.name=="g0_p108_composition.json" else persisted)
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=read):
            self.assertFalse(check_p112._historical_p108())
        def fail():
            raise RuntimeError("historical derivation refused")
        module.derive=fail
        with patch.dict(sys.modules,{"check_p108_verdict":module}), \
             patch.object(check_p112,"_json",side_effect=read):
            self.assertFalse(check_p112._historical_p108())

    def test_validator_rejects_presence_and_container_denominator_mutations(self):
        cases={case["id"]:case for case in check_p112.oracle.generate_cases()}
        case=cases["share_equal"]
        expected=check_p112.oracle.evaluate_target(case,1)
        total=expected["container_total_activity_bq"]
        nuclides=[]
        for item in expected["nuclides"]:
            p61=check_p112._expected_predicate_evidence(case,expected,item,"unlisted_concentration")
            wac=check_p112._expected_predicate_evidence(case,expected,item,"wac_fraction")
            nuclides.append({"nuclide":item["nuclide"],"activity_bq":item["activity_bq"],
                "concentration_bq_cm3":item["concentration_bq_cm3"],
                "activity_share":item["activity_bq"]/total,"presence":item["presence"],
                "part61_listing":p61["part61_listing"],"wac_listing":p61["wac_listing"],
                "wac_concentration":None if wac["concentration"] is None else
                    {"value":wac["concentration"],"unit":wac["unit"]},
                "predicates":{name:{"status":entry["value"],"reasons":[entry["reason"]],
                                    "evidence":check_p112._expected_predicate_evidence(case,expected,item,name)}
                              for name,entry in item["predicates"].items()}})
        rows=[{"row_id":row["row_id"],"members":row["members"],"contributions_bq":
               {name:expected["inventory_activity_bq"][name] for name in row["members"]
                if name in expected["inventory_activity_bq"]},"membership_complete":row["membership_complete"],
               "applicability":row["presence"],"unit":row["unit"],"selected_class":row["column"],
               "limit":row["limit"],"known_activity_bq":row["activity_bq"],
               "known_concentration":row["concentration"],"relation":row["relation"],
               "assessment_indicator":row["assessment_indicator"]} for row in expected["rows"]]
        target={"step":1,"t_s":0.0,"nominal_class":expected["classification"]["class"],
                "inventory_coverage":"complete","unbounded_inventory_reasons":[],
                "inventory_activity_bq":expected["inventory_activity_bq"],
                "external_tritium_activity_bq":0.0,
                "known_total_activity_bq":sum(expected["inventory_activity_bq"].values()),
                "container_total_activity_bq":expected["container_total_activity_bq"],
                "container_denominator_complete":True,
                "dot_rq_mixture_ratios":expected["dot_rq_mixture_ratios"],
                "dot_rq_mixture_ratio":expected["dot_rq_mixture_ratio"],
                "dot_rq_mixture_complete":expected["dot_rq_mixture_complete"],
                "assessment_indicator":expected["assessment_indicator"],
                "coverage":"complete","denominator_missing_reasons":[],"nuclides":nuclides,"rows":rows}
        self.assertEqual(check_p112._validate_target(case,expected,target,"baseline"),[])
        target["nuclides"][0]["presence"]="not-the-independent-value"
        self.assertTrue(check_p112._validate_target(case,expected,target,"mutated"))


if __name__=="__main__":
    unittest.main()
