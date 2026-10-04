"""P111 seal round-trip regressions; source-only, no subprocesses."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p111


class SealRoundTripTests(unittest.TestCase):
    def test_real_write_read_replay_then_tamper_rejected(self):
        base={"schema":"actinv-p111-intrusion-screen-g0-1","pass":True,
              "phase":"P111","case_count":52,"target_count":53,
              "control_sha256":{"x":"a"*64}}
        with tempfile.TemporaryDirectory() as temp:
            seal=Path(temp)/"g0.json"
            with patch.object(check_p111,"G0",seal), patch.object(check_p111,"_g0_base",return_value=base):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check_p111.g0(seal=True),0)
                    self.assertEqual(check_p111.g0(no_write=True),0)
                    changed=json.loads(seal.read_text(encoding="utf-8"))
                    changed["case_count"]+=1
                    seal.write_text(json.dumps(changed),encoding="utf-8")
                    self.assertEqual(check_p111.g0(no_write=True),1)

    def test_validator_rejects_presence_and_container_denominator_mutations(self):
        cases={case["id"]:case for case in check_p111.oracle.generate_cases()}
        case=cases["share_equal"]
        expected=check_p111.oracle.evaluate_target(case,1)
        total=expected["container_total_activity_bq"]
        nuclides=[]
        for item in expected["nuclides"]:
            p61=check_p111._expected_predicate_evidence(case,expected,item,"unlisted_concentration")
            wac=check_p111._expected_predicate_evidence(case,expected,item,"wac_fraction")
            nuclides.append({"nuclide":item["nuclide"],"activity_bq":item["activity_bq"],
                "concentration_bq_cm3":item["concentration_bq_cm3"],
                "activity_share":item["activity_bq"]/total,"presence":item["presence"],
                "part61_listing":p61["part61_listing"],"wac_listing":p61["wac_listing"],
                "wac_concentration":None if wac["concentration"] is None else
                    {"value":wac["concentration"],"unit":wac["unit"]},
                "predicates":{name:{"status":entry["value"],"reasons":[entry["reason"]],
                                    "evidence":check_p111._expected_predicate_evidence(case,expected,item,name)}
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
        self.assertEqual(check_p111._validate_target(case,expected,target,"baseline"),[])
        target["nuclides"][0]["presence"]="not-the-independent-value"
        self.assertTrue(check_p111._validate_target(case,expected,target,"mutated"))


if __name__=="__main__":
    unittest.main()
