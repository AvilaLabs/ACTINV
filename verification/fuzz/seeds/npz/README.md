# Synthetic NPZ seed

`small-library.npz` contains no evaluated nuclear data. It was assembled with
Python's standard `struct` and `zipfile` modules, independently of ACTINV's NPZ
writer. Members use NPY v1, little-endian values, C order, and stored ZIP entries:

- `rows.npy`: int64 shape (1, 5), values [260560, 102, 0, 0, 3].
- `sig.npy`: float64 shape (1, 2), values [1, 2].
- `bounds.npy`: float64 shape (3,), values [1, 2, 3].

The seed reachability test checks that the production reader accepts this exact
fixture. The corpus script copies the tested bytes into the writable fuzz corpus.

The distribution control permits only this path with SHA-256
`074f20da9214bf87d43196a444498571e11b5e9096d42be9f15b26299dfa7b11`.
Its regression independently decodes the arrays and rejects both an altered
fixture and an unlisted copy. Other archives remain subject to the bulk-data guard.
