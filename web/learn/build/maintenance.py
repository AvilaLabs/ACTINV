import json
import subprocess

# Plan at four values put on a person-sievert; the plan is JSON, so change it in Python and write it back.
plan = json.load(open("plan.json"))
for per_sv in [2e5, 1e6, 5e6, 2e7]:
    plan["cost"]["per_person_Sv"] = per_sv
    json.dump(plan, open("plan-try.json", "w"))
    subprocess.run(["actinv", "maintenance", "plan-try.json", "plan-try-result.json"], check=True)
    best = json.load(open("plan-try-result.json"))["best"]
    print(f'{per_sv:9,.0f} per person-Sv: wait {best["wait_d"]:5.1f} days, crew dose {best["collective_Sv"] * 1000:4.1f} mSv')
