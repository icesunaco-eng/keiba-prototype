# Isolated prediction boundary proposal
Status: local prototype, NOT integrated with System, NOT production or a real prediction model.

`python3 -I -B boundary/test_boundary.py` runs 19 synthetic tests.
An internal independent reviewer reproduced all 19 and added 22 cases (all passed): strict bool types, blank source, invalid datetime, nested result fields, future-information boundaries, and real-shadow schema hold. No network/child process attempts observed. This is limited boundary verification, not prediction accuracy or full security verification.

Real model and feature schema are unimplemented. `real_shadow` is always held with `payload_schema_unimplemented`; no production eligibility or orders. Source/rights flags do not establish provenance by themselves. Freshness integration and verified source evidence remain unfinished. Synthetic inputs must never be recorded as real predictive performance.

Separate normal-path wiring check using an already cached result produced a single payout import after three deliveries and zero real predictions/official performance. A separately fabricated scenario saved 1h/15m checkpoints and settled only the final 15m checkpoint once. The prototype core is still `dummy-fixed`; this check does not establish real prediction ability. The real document, raw payloads, local paths, and result-dependent harness are intentionally not uploaded.

Hashes of reviewed files:
- prediction_boundary.py: aca280b20ca843878ebfb15844d5be475bbf116231af599860bfeb47b263ef79
- test_boundary.py: ed8ad63896d45accb4c2484e6679cf048d72f3466fc6a2071803aac637307bdd

Project record: https://app.notion.com/p/417f1dff94dc8174b62cdb6b7c3de202
This branch is isolated and not merged. No real betting, new data acquisition or production changes.
