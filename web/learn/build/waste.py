import json
import subprocess

subprocess.run(["actinv", "impurities", "waste-screen.json", "waste-screen-result.json"], check=True)
result = json.load(open("waste-screen-result.json"))

for target, label in zip(result["targets"], ["1 day", "1 year", "10 years", "100 years"]):
    c = next(e for e in target["endpoints"] if e["response"] == "waste_class:C")
    table1 = next(t["normalized_sum"] for t in c["nominal"]["target_class_constraints"] if t["table"] == 1)
    print(f'{label:9}  class {c["nominal"]["class"]}  (table 1 sum {table1:.3f})  worst case {c["worst_case"]["class"]}')
