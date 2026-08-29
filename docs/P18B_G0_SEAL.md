# P18b G0 — authority, source provenance, and quarantine seal

P18b G0 passes. It freezes the inputs needed to distinguish source-format quality from safe runtime use before any
new per-file conservation classification is read.

## What is sealed

- P18 closed at `d3456890cf0c4b9221ebf17f6630ef8b4fe768cc`; GitHub Actions run `33258605964` passed all
  43 substantive steps while retaining `P18-FAIL`.
- P18b opened at `bf540efc3cd9525d17f69a525ab6732c648bfe93`; opening run `33259343493` also passed all
  43 substantive steps.
- The P18b protocol is byte-frozen at
  `69076fa2656b239addbb15fbb4727caaa2c8ea37b3aa82a141f3a2b0b619eabe`.
- ENDF-102 remains pinned at
  `77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022`.
- The official IAEA ENDF utility codes are pinned at commit
  `c2a6718bd831b5c8a6e975beb1946954b1d73c40`; the exact CHECKR, FIZCON, README and license file hashes are
  recorded in `results/g0_p18b_seal.json`.
- Every P18 session, verdict, family-seal, corpus-audit and changed-identity artifact is rehashed without changing
  its failed verdict.

The deterministic `results/p18b_source_manifest.json.gz` contains only provenance: file name, byte count and
SHA-256 for all 11,400 frozen TENDL working files. It contains no evaluated record values or generated library. Its
canonical JSON is 1,720,690 bytes with SHA-256
`d5be79122efe4cde4f4aed2219fe64524476fcb0aa4231fd3ab0d582cf09fe4c`; deterministic gzip is 564,612 bytes with
SHA-256 `ca593eb38a125883f09502fc3083153e64aff21ec22f19f1348daf1fd328fe7d`.

## Official-checker sample

The corpus sample is frozen before CHECKR or FIZCON output is read. It contains:

- the 25 lowest SHA-256 ranks per projectile under the protocol seed;
- each of the four already published P18 worst-case files; and
- every source file carrying one of P18's 143 already recorded excitation conflicts.

Overlaps are collapsed, producing 245 files: 70 alpha, 51 deuteron, 54 neutron and 70 proton. The canonical sample
SHA-256 is `248387f23afdc30e1beb4ab3ac0348653b5675d049063f0a0c3134e9c1abb055`. All generated G1 fixtures are also in
scope when they exist. The official sample is an independent compatibility control; ACTINV's own G2 audit will still
cover every file.

## Quarantine and independent check

No Rodrigo diagnostic value, Rodrigo held-out value, new checkpoint classification or official-checker output was
read in G0. The five P18 Amendment 1 families remain forced diagnostic and the remaining held-out partition remains
sealed.

`controls/check_g0_p18b.py` imports no production or G0 producer module. It independently decompresses and
canonicalizes the source manifest, reconstructs the sample from its frozen seed and prior P18 evidence, binds both
green workflows and all authority hashes, and rejects seven mutation plants: protocol, workflow, quarantine, sample,
conflict-reason, dependent-field and manifest changes.

G0 authorizes only G1's generated decimal/checker oracle. It does not authorize reading per-file G2 classifications,
measurement values, changing runtime behavior or publishing a release.

## Reproduction

With the pinned manual, public TENDL staging manifests and official utility-code checkout available at their external
paths:

```text
python controls/g0_p18b_seal.py
python controls/check_g0_p18b.py --no-write
```
