# decide: is the design certified against its limits?

`actinv decide` solves one banded run (1 g of iron, FNS 14 MeV spectrum, 5 minutes of irradiation, then 1 d and
30 d of cooling) and says, limit by limit, whether the upper edge of the nuclear-data band stays under the limit.
The two limits in `decision.json` (total activity of 1500 Bq/g after 1 d, 500 Bq/g after 31 d) are illustrative,
set by hand; edit them to see the verdict change.

Run from the repository root. The run needs the standard data and the matching covariance bundle:

```sh
actinv data fetch
actinv data fetch tendl-2025-neutron-covariance
actinv decide examples/decide/decision.json verdict.json
```

`decide` does not resolve `catalog:` references, so `run_spec.json` names the files under `actinv-data/v1.2.0/`
directly. Run from the directory that holds `actinv-data`.

Expected runtime: one solve with first-order bands, seconds once the data are installed; the first run on a machine
also builds the prepared-data cache, which takes longer. Not timed.

In `verdict.json` read `verdict.certified` and `verdict.statement`, then each entry of `constraints`: `value` is the
band edge, `violation` is negative when the limit holds, and `nominal_satisfied` shows whether the nominal value alone
would have passed. `measurements` lists which nuclear-data parameters a new measurement would help most.
