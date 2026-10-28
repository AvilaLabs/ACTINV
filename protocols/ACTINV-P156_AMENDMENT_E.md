# P156 Amendment E — the v1.2.0 notice is a new artifact a fetch must download

Registered after evidence run 1, which failed one G7 sub-check and is preserved under `results/failures/p156/`.
This is the phase's one repair round. Every other gate and criterion is unchanged.

## Finding that requires it

Evidence run 1 (candidate `354138a`) passed G0 and G2–G6 and the G7 checks of catalog 1.2.0, the staged assets and
the published release. It failed one G7 sub-check: "A fetch into a directory holding the v1.1.0 files downloads only
the table." The fetch downloaded two artifacts, `tendl-2025-neutron-damage` (49,041,569 bytes) and
`actinv-data-notice-v1-2`, the v1.2.0 data notice (8,440 bytes). It copied every v1.1.0 file and left the v1.1.0
directory untouched. The notice is new in catalog 1.2.0: it is the notice the neutron bundles now reference, and it
discloses the table's sources. No v1.1.0 installation holds it, so no fetch can avoid downloading it. The protocol
sentence did not account for the new notice. The checker's own expected set already left notices out, but its
final test did not.

## E1. G7 fetch condition

A fetch into a directory holding the v1.1.0 files downloads only the artifacts that are new in catalog 1.2.0: the
table and the v1.2.0 notice. Every artifact catalog 1.1.0 already holds is copied, not downloaded, and the v1.1.0
files are left untouched. The rest of G7 is unchanged.
