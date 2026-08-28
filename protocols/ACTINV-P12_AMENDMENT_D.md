# ACTINV P12 Amendment D — bounded production-reader inputs

**Date:** 2026-08-27. **Trigger:** The required P12-G3 smoke preparation reviewed each production reader before the
fixed full partition. Five input shapes could reach an unchecked index, capacity request, byte slice or legacy record
walk before the reader had proved that the record was valid and its declared payload existed. Under the gate's 1 GiB
process ceiling those paths could terminate the process instead of returning an ordinary parse error.

## Append-only discovery record

1. Checked ENDF LIST/TAB1/TAB2 helpers reserved from declared counts before proving that enough fixed-width records
   remained; TAB1/TAB2 count multiplication was not checked.
2. Legacy fixed-width ENDF field/tail helpers could slice an otherwise valid UTF-8 string at a non-character byte
   boundary instead of rejecting the non-ASCII record.
3. The decay reader still used legacy unchecked LIST/TAB1 helpers and reserved declared spectrum counts before
   bounding them by the section.
4. The fission-yield reader reserved the declared incident-energy count before bounding it by the section and used
   unchecked payload-index arithmetic.
5. The activation-library reader could reserve from an NPY header shape before proving that the corresponding
   uncompressed ZIP member could contain that payload.

## Frozen repair

1. Reject non-ASCII fixed-width records before byte slicing. Prove every declared payload against the remaining
   section, use checked count/index arithmetic, and only then reserve. Preserve the existing checked ENDF record
   implementations and scientific field mapping.
2. Route decay and fission path readers through new in-memory entry points that contain the same production parser;
   do not introduce a second parser. Require complete record consumption.
3. Route path and byte-buffer activation-library reads through one generic ZIP reader. Compare every declared NPY
   payload size with the member's uncompressed size before allocating.
4. Retain minimized regression tests for non-ASCII fixed-width records, oversized ENDF payload declarations, decay
   spectrum counts, fission incident-energy counts and NPY shapes. The fixed smoke partition must repeat
   byte-identically; the full fixed partition must then complete below 1 GiB with no process-level failure.

This amendment changes error handling for invalid or truncated inputs only. It changes no accepted nuclear-data
value, physics method, solver result, tolerance, package interface or public-release authority. P12 remains eligible
only for **P12-CONDITIONAL** closure.
