# Building the course

`web/learn` is the interactive course served at `/learn/` (`scripts/prepare_web.py` copies it into the web
bundle, without this folder). The pages are static HTML, CSS and JavaScript modules; nothing is compiled.
Fonts are the latin subsets of Bricolage Grotesque, Source Serif 4 and IBM Plex Mono (SIL OFL 1.1).

## Chapter 1: Your first irradiation

`make_bracket.py` writes the problem the learner downloads (`first-irradiation/files/bracket.json`) and the
results the page plots (`first-irradiation/data/bracket-data.json`): eight ACTINV runs of the bracket, one per
cobalt level. From the repository root, with `actinv-data/` installed and a release build:

```sh
python3 web/learn/build/make_bracket.py --actinv target/release/actinv --work target/learn-work
```

Run it inside the bounded scope from `AGENTS.md`. `scripts/test_prepare_web.py` fails when the data no longer
belongs to the problem file.

The page replays three recorded outputs, all printed by ACTINV 1.7.2 for this problem:

- the practice terminal (`chapter.js`, `OUT` and `FETCH`): `actinv`, `actinv help run`, `actinv data fetch`
  in an empty folder, `actinv validate`, `actinv run`, `actinv report result.json --tables dominant --format
  text --step 14`, and the error messages for a missing file, an unknown command and missing data;
- the Python tab: `first.py` and `study.py` in this folder, run with the `actinv` 1.7.2 package from PyPI in
  the folder holding `bracket.json` and `photon-response.json`.

After changing the problem or ACTINV's output, rerun these and update the recorded text.

## The neutron field

The bracket's spectrum is illustrative: 25% of the flux in the 14.1 MeV line, 55% in the 1/E shape and 20% in
the thermal shape of `actinv new --source`, 1e10 n/cm2/s in total. X-rays are excluded from the photon source
(`photon.exclude_xrays`), because the steel absorbs them; without that, Fe-55's X-rays dominate a point-source
dose with no attenuation in the part.
