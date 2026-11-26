# A RouterOS 7 capture, on purpose

Neither switch runs RouterOS 7 yet (card 696 moves them). This directory is a synthetic capture
marked `7.24.7` so the v7 export path — the bare `/export`, where sensitive values are hidden by the
device itself — is exercised by a test instead of by a v6 device pretending to be one.

`crs326.export.txt` uses the v7 absolute-line shape and a v7-only setting
(`/ip ssh set password-authentication=...`). The device name matches a real target so the default
`NET_AUDIT_TARGETS` resolves it; `hack/tests/test_network_audit.py` points
`NET_AUDIT_FROM_FIXTURE` here.
