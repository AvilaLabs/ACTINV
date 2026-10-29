# waste bounds: which Part 61 classes does a declared activity range allow?

`actinv waste bounds` classifies a 1 kg activated-metal component whose C-14 and Nb-94 activities are only known as
ranges, and reports the set of classes the range could fall in. All values are artificial.

```sh
actinv waste bounds examples/waste_bounds/bounds.json bounds-result.json
```

Expected runtime: well under a second; no data files are read.

In `bounds-result.json` look at each target's `class_envelope` (the classes the box can reach),
`class_is_stable` and `stable_class`, and the `row_fraction_ranges` that show which nuclide moves the class. The
envelope is a conservative superset: classes in between are not asserted to be feasible.
