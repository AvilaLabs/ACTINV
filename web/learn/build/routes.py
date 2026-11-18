from actinv import Problem, solve

problem = Problem.from_file("routes.json")
result = solve(problem)

for target in result["pathway_routes"]["targets"]:
    top = target["paths"][0]                      # the biggest route
    chain = " -> ".join(top["nuclides"])
    print(f'{target["target"]:5}  {top["fraction"]:4.0%} from {chain}  {", ".join(top["edges"])}')
