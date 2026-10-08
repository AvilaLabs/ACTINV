# P120 Amendment B — one live guard missed: controls/test_p116_seal.py

Registered 2026-10-07, after P120-PASS and the P121 push, before any edit to
`controls/test_p116_seal.py`.

## What happened

P120 (PASS, `0287087`) changed the P116/P117/P118 history checks so they verify
pinned Git commits instead of the live working tree. P121 (`1b91e5f`) then changed
`crates/actinv-core/src/photon.rs`. CI's controls job failed in the step "P116
unchanged twin verification successor controls". The cause:
`controls/test_p116_seal.py`'s `setUpClass` calls `controls._g0_base()`, which by
default reads the live Rust, so two tests fail. P120 missed this file. CI stops at
the first failing step, so the later steps have not run on P121.

## The change

`test_p116_seal.py` builds its shared report with
`check_p116._g0_base(verify_current_sources=False)`: the sealed production map plus
the `2117f3b` Git objects, the mode P120 introduced. Every assertion stays as it is.

## The evidence required

The CI steps P116, P117 and P118 must each exit 0 locally with the P121 tree, then
CI must be green on the pushed commit. Run all fourteen CI history steps P103–P118
as listed in `ci.yml`. Any further live guard found that way gets its own amendment
before it is edited.

## The lesson

Future scope probes run whole CI steps with a real source change.
