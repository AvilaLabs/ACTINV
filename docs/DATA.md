# Data sources, terms, hashes

ACTINV never bundles nuclear data. Each run fetches (or points at) public files and records SHA-256 hashes in its
certificate. Terms of use are those of the hosts; check them before redistribution.

| data | host | notes |
|---|---|---|
| EAF-2010 neutron activation (816 targets) | IAEA-NDS `public/download-endf/EAF-2010/n/` | pointwise-complete MF=3, MF=8/9/10 isomer branching |
| TENDL-2017…2025 | IAEA-NDS `public/download-endf/TENDL-20xx/n/` | resolved resonances need reconstruction (own code) |
| FENDL-3.2c (ENDF-6, ACE, GENDF, NJOY inputs) | IAEA-NDS `fendl/` | transport library; used as the NJOY reference for our reconstruction |
| ENDF/B-VIII.0 decay sublibrary | IAEA-NDS mirror bulk zip | primary decay data (3,821 materials) |
| JEFF-3.3 radioactive decay data | IAEA-NDS mirror bulk zip | fallback (3,852 materials) |
| FNS decay-heat benchmark set | IAEA CoNDERC `conderc/fusion/files/fns.zip` | 73 materials, 132 experiments; spectra, measurements, FISPACT-II reference runs |
| natural abundances / atomic masses | copied from `openmc.data` (MIT) with its citations | independent re-verification pending |
| FISPACT 709-group boundaries | `pypact` (Apache-2.0) | |

Local layout used by the controls: `~/nuclear-data/{eaf-2010, tendl-eaf-test, fendl-3.2c, endfb-viii.0-decay,
jeff-3.3-decay, conderc-fns}` with `MANIFEST*.sha256` files.
