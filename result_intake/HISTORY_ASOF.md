# Conditional history snapshot gate

Offline integration only. `history_asof.select` accepts validated `profile_history.batch` output and an explicit hypothetical or confirmed target/cutoff. It does not acquire data or perform predictions.

A snapshot acquired after cutoff is excluded, even when its race dates are old. Only race dates strictly before both cutoff and target in Japan time are candidates. Exact historical start/publication times remain null; acquisition is evidence of observed availability, not an invented publication date. Same-day histories are conservatively excluded. Target identity/confirmed entry checks remain required and never auto-clear: model_ready and production_ready stay false.

Real saved 2-profile / 9-run fixture: hypothetical 2026-11-14 cutoff yields 0 candidates and 9 exclusions. Hypothetical 2026-11-17 cutoff yields 9 conditional candidates, not prediction input; future target is synthetic, not a claimed confirmed race. No new acquisition. 120 tests pass including late snapshots, target self, same-day exclusion, timestamp mismatch, duplicates and nonmutation. Independent review pending.
