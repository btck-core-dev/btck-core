# Public release metadata

Published replay evidence references use neutral numbered labels and unchanged SHA256 digests. Original private replay records retain their environment identifiers; validation still requires distinct environments, matching checkpoint/state fields, PASS and asset conservation. New approvals sort references by evidence digest, so labels do not depend on hostname spelling or argument order.

`approve_mainnet_checkpoint.py --include-environment-ids` reproduces legacy label output when a previous signed approval must be reconstructed. Do not use that option for new public approvals containing private machine identifiers.

The public label update changes manifest file bytes and its file hash. It does not change genesis, consensus parameters, checkpoints, release approval, feature flags or the runtime consensus fingerprint. Existing signed release assets, frozen replay records and deployed manifests remain unchanged. The revised file belongs in a future reviewed release.

Removing labels from the current source does not remove already published commits, tags, downloads or external copies. This patch does not rewrite repository history or replace production files.
