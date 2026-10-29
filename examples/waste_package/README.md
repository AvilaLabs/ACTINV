# waste package: what is the class of one container of mixed items?

`actinv waste package` classifies one container that holds a steel plug, two pieces sectioned from one rod and a
Cs-137 sealed source under the NRC concentration averaging and encapsulation position (BTP, Revision 1). The
activities are declared whole-item Bq at two steps (1 and 2); they are artificial. An item can instead take
`"activity": {"result": "run-result.json"}`, the per-gram activities of an `actinv run` result at the target step
times the item mass, but then `nuclide_properties` must cover every nuclide with positive activity.

```sh
actinv waste package examples/waste_package/package.json package-result.json
```

Expected runtime: well under a second; no data files are read.

In `package-result.json` look at `steps[].package_class` and `status`, `steps[].groups` (items grouped by waste type,
with the sums of fractions and the averaged class) and their `removed_items` (items taken out of the average and
classified alone; the Cs-137 source is the likely one), and `steps[].sections` (whether the two rod pieces were
merged).
