from actinv import Problem, solve

problem = Problem.from_file("bracket.json")
steel = problem["material"]["composition"]

for cobalt in [0.0, 0.05, 0.1, 0.2]:          # weight percent
    steel["Co"], steel["Fe"] = cobalt, 66.9 - cobalt
    result = solve(problem)
    ten_years = result.steps[34]["photon_source"]["point_source_dose"]
    print(f"Co {cobalt:4.2f}%  ->  {ten_years['gamma_air_dose_Sv_h'] * 1e6:6.2f} µSv/h after 10 years")
