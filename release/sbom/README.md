# Release SBOM output

CycloneDX SBOMs are generated from the exact verified release checkpoint and are not
kept as stale source artifacts. A release job must generate fresh documents for:

- the npm Player workspace;
- the Player Rust crate;
- the `lrail-format` Rust crate and CLI;
- the pinned Python runtime.

Attach generated SBOMs and their hashes to the release evidence. Do not reuse SBOMs
from the removed Studio or volume-broker graph.
