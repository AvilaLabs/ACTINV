# ACTINV CI input cache: source and attribution notice

This notice accompanies the exact-byte assets listed in
[`ci_data_seed.json`](https://github.com/AvilaLabs/ACTINV/releases/download/ci-data-cache-2026-10-04-v1/ci_data_seed.json). The cache is a
transport copy for the pinned CI inputs; it does not modify, transform,
replace, or relicense those inputs. The manifest retains each provider's
original source URL, byte count, and SHA-256 identity. The separate
`ci-data-cache-2026-10-04-v1` release is not an official release of any data
provider and does not imply provider endorsement of ACTINV.

## Source attribution

- **TENDL-2023 neutron evaluations:** A.J. Koning, D. Rochman, V. Raffuzzi, J.-C. Sublet,
  and the TENDL collaboration. The ten Fe-52 through Fe-59 files are the
  TENDL-2023 files identified individually in the seed manifest. Source:
  [TENDL-2023](https://tendl.imperial.ac.uk/tendl_2023/tendl2023.html) and the
  [IAEA-NDS download mirror](https://www-nds.iaea.org/public/download-endf/TENDL-2023/n/).
  TENDL's terms and any file-specific ENDF-6 identification-record terms apply
  to these original evaluation files.
- **ENDF/B-VIII.0 radioactive-decay data:** D.A. Brown et al.,
  “ENDF/B-VIII.0: The 8th Major Release of the Nuclear Reaction Data Library
  with CIELO-project Cross Sections, New Standards and Thermal Scattering
  Data,” *Nuclear Data Sheets* 148 (2018) 1–142,
  [doi:10.1016/j.nds.2018.02.001](https://doi.org/10.1016/j.nds.2018.02.001).
  Source: [IAEA-NDS decay sublibrary archive](https://www-nds.iaea.org/public/download-endf/ENDF-B-VIII.0/_backup-by-NSUB/zip/endf-b-viii-0_decay.sublib.zip).
- **JEFF-3.3 radioactive-decay data:** A.J.M. Plompen et al., “The joint
  evaluated fission and fusion nuclear data library, JEFF-3.3,” *European
  Physical Journal A* 56 (2020) 181,
  [doi:10.1140/epja/s10050-020-00141-9](https://doi.org/10.1140/epja/s10050-020-00141-9).
  Source: [IAEA-NDS JEFF-3.3 decay sublibrary archive](https://www-nds.iaea.org/public/download-endf/JEFF-3.3/_backup-by-NSUB/zip/jeff-3-3_decay.sublib.zip).
- **FNS experiment source archive:** Japan Atomic Energy Research Institute
  (JAERI) FNS source materials distributed through
  [IAEA CoNDERC](https://www-nds.iaea.org/conderc/fusion/files/fns.zip).
  The archive includes experimental measurements, spectra, input decks, and
  reference material; the ACTINV case pins the archive and the members it
  uses in `examples/fns_iron/case.json`.

The [IAEA website Terms of Use](https://nucleus.iaea.org/Pages/Others/Terms-Of-Use.aspx)
allow copying, downloading, and dissemination of IAEA site materials with
appropriate source acknowledgement and without implying IAEA endorsement,
subject to material-specific restrictions. The same terms distinguish
third-party material and require attention to the rights holder's terms.
Accordingly, original-provider and any file-specific terms remain applicable
to each cached asset. This notice makes no blanket claim that all inputs share
one license and grants no new rights in third-party material.

ACTINV software is licensed separately. No ACTINV software license, including
the licenses for the source repository or native data-installation code,
replaces or changes the terms attached to these data files.
