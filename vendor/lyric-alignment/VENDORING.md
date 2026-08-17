# Vietnamese song aligner vendoring

LyricRail vendors only the inference model definition required at runtime and
the corresponding upstream license. Training scripts, notebooks, datasets,
model weights, caches, and repository metadata are intentionally excluded.

- Upstream: https://github.com/nguyenvulebinh/lyric-alignment
- Upstream commit: `b8bca25320d39f4a65e7ff3eee3e93382faaef9b`
- Included source: `model_handling.py`
- Source SHA-256: `ed4178006be431a3f5b51558ec36e3e3557d51dd83e181d5c0d91c312537f83b`
- Included license: `LICENSE` (Apache License 2.0)
- License SHA-256: `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`

Model weights are not vendored. Their repository revision and file digest are
pinned separately in `config/model-manifest.json`.

When updating the vendored source, start from a clean upstream checkout, review
the upstream diff, update the commit and both digests above, retain the license,
and run the full LyricRail validation and forced-alignment test suite.
