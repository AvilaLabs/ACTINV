from actinv import Problem, solve

problem = Problem.from_file("bracket.json")   # read the problem file
result = solve(problem)                       # run it: about 20 seconds

one_day = result.steps[13]                    # step 14: one day after shutdown
dose = one_day["photon_source"]["point_source_dose"]["gamma_air_dose_Sv_h"]
print(f"Dose rate 1 m away, one day after shutdown: {dose * 1000:.2f} mSv/h")
