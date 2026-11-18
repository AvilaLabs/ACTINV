import json
import subprocess

# actinv impurities has no Python function of its own; it's one command, and its result is JSON.
subprocess.run(["actinv", "impurities", "limit.json", "limit-result.json"], check=True)
result = json.load(open("limit-result.json"))

dose = result["targets"][0]["endpoints"][0]
print(f'Limit: {dose["limit"]} {dose["unit"]} after 10 years')
for name, imp in dose["impurities"].items():
    alone = imp["limits"]["alone"]
    if alone["status"] == "limit":
        print(f'{name:3} at most {alone["wt_pct"]:.3f} wt%')
    else:
        print(f'{name:3} can\'t reach the limit on its own')
