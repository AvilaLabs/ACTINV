# P156 Amendment B — negative 0 K reconstructions clipped as NJOY RECONR does; failures listed, not fatal

Registered after development run 1 (disclosed in the ledger) and before any evidence run. Gates, tolerances and the
target selection are unchanged except as stated.

## Finding that requires it

Development run 1, the first build of the shipped table with the item-6 command on candidate `1a04639`, stopped after
2 min 51 s:
`n-Fe054.tendl: MT2 resonance processing: MT=2 zero-K reconstruction is nonfinite or negative at 3493.98917819688 eV`.
The 0 K resonance reconstruction of TENDL-2025 Fe-54's elastic cross section goes negative near 3.49 keV, and the
shared processing code refuses any value below -1e-10 barn. The activation library never met this, because it
processes only MT=18 and MT=102 from MF=2, at 293.6 K; the damage table is the first to reconstruct MT=2. P154's four
targets did not have it. Fe-54 is 5.8 % of natural iron, so the table cannot ship without it, and a directory build
that stops at its first defective file cannot show which other selected files have defects.

NJOY2016 RECONR sets a negative resonance-range cross section to zero (`reconr.f90`, 2016.79, line 2643:
`if (sigp(j).lt.zero) sigp(j)=0`), so an NJOY-processed MT=444 already carries this treatment.

## B1. Clipping in the damage build

The kinematic damage build sets a finite negative 0 K reconstructed value to zero. Nonfinite values remain errors. The
activation-library builder keeps its fail-closed behaviour and its results. Each clipped reaction is recorded in its
target's row as `zero_k_clipped`: MT, number of points, most negative value and its energy, and the energy range of
the clipped points. A target with no clipped point is unchanged, byte for byte.

## B2. Failures listed

The build command of item 6 gains `--continue-on-error`. An evaluation that still fails is listed in the table's
`failed` array with its SHA-256 and error, and the build continues. G5's "listed with its reason" is satisfied by that
entry. G5 also reports the number of failed and clipped targets and, for each, the selected nuclide's natural
abundance where it has one. A failed target that is a stable nuclide of Fe, Cr, Ni, W, V, Ti, Ta, Mo, Nb, Mn, Co, Cu,
Al, Si, Be or Li fails G5, since those are the structural and breeder elements the table is shipped for.

## B3. G4 and G2

G2's byte comparisons and G4's NJOY comparisons are unchanged. G4 additionally reports any `zero_k_clipped` record of
its twelve targets beside the NJOY results.
