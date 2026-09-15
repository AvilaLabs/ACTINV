#!/usr/bin/env python3
"""Small analytical LU/CRAM diagnostic. No subprocesses or physical-data tuning."""
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csc_matrix
from openmc.deplete.cram import Cram16Solver,Cram48Solver
import cram_ref

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'results/hyperion-numerics-fix'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for order,solver in [(16,Cram16Solver),(48,Cram48Solver)]:
        coeff={'alpha0':solver.alpha0,'alpha':[complex(v) for v in solver.alpha],
               'theta':[complex(v) for v in solver.theta]}
        for stiffness in [1e-6,1.,100.,1e4,1e8,1e12]:
            for background in [1.,1e12,1e21,1e24]:
                for reverse in [False,True]:
                    parent,daughter=(1,0) if reverse else (0,1)
                    trip=[(parent,parent,-1.),(daughter,parent,1.)]
                    initial=[0.,0.];initial[daughter]=background
                    got=cram_ref.cram_step(2,trip,initial,stiffness,coeff)
                    matrix=csc_matrix(([v for i,j,v in trip],([i for i,j,v in trip],[j for i,j,v in trip])),shape=(2,2))
                    independent=solver(matrix,np.array(initial),stiffness)
                    pivots=[]
                    for theta in coeff['theta']:
                        m=cram_ref.CSC(2,[(i,j,stiffness*v) for i,j,v in trip]+[(j,j,-theta) for j in range(2)])
                        pivots.append(cram_ref.lu(m)[-1])
                    rows.append({'order':order,'stiffness':stiffness,'background':background,'reverse':reverse,
                        'custom_parent':got[parent],'scipy_parent':float(independent[parent]),
                        'custom_stable_relative_error':abs(got[daughter]-background)/background,
                        'row_exchanges':sum(p!=[0,1] for p in pivots)})
    (OUT/'initial_diagnosis.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(sorted(rows,key=lambda r:abs(r['custom_parent']),reverse=True)[:12],indent=2))


def alternatives():
    rows=[]
    for order,solver in [(16,Cram16Solver),(48,Cram48Solver)]:
        for stiffness in [1e-6,1.,100.,1e4,1e8,1e12]:
            for background in [1.,1e12,1e21,1e24]:
                for mode in ['refine','equilibrate']:
                    y=[0.,background]
                    for theta,alpha in zip(solver.theta,solver.alpha):
                        theta,alpha=complex(theta),complex(alpha)
                        trip=[(0,0,-stiffness-theta),(1,0,stiffness),(1,1,-theta)]
                        scaling=[max(abs(v) for i,j,v in trip if i==k) for k in range(2)] if mode=='equilibrate' else [1.,1.]
                        matrix=cram_ref.CSC(2,[(i,j,v/scaling[i]) for i,j,v in trip])
                        fac=cram_ref.lu(matrix)
                        rhs=[complex(v)/s for v,s in zip(y,scaling)]
                        z=cram_ref.solve(fac,rhs)
                        if mode=='refine':
                            for _ in range(3):
                                residual=rhs.copy()
                                for i,j,v in trip:residual[i]-=v*z[j]
                                correction=cram_ref.solve(fac,residual)
                                z=[v+d for v,d in zip(z,correction)]
                        y=[v+2*(alpha*a).real for v,a in zip(y,z)]
                    y=[v*solver.alpha0 for v in y]
                    rows.append({'order':order,'stiffness':stiffness,'background':background,'mode':mode,
                                 'parent':float(y[0]),'stable_relative_error':float(abs(y[1]-background)/background)})
    (OUT/'diagnostic_alternatives.json').write_text(json.dumps(rows,indent=2)+'\n')
    for mode in ['refine','equilibrate']:
        print(mode,json.dumps(sorted([r for r in rows if r['mode']==mode],key=lambda r:abs(r['parent']),reverse=True)[:5],indent=2))

if __name__=='__main__':
    import sys
    alternatives() if '--alternatives' in sys.argv else main()
