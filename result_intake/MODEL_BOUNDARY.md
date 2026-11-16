# Practical remaining boundary

Existing synthetic_flow/model.py accepts only synthetic=True and eight explicit 0..10 feature groups (ability/recent/distance/course/going/pace/draw/weight_rider). It is an untrained equal-weight rule demonstration, not a real calibrated model. Historical placing/counts cannot legitimately be relabeled as these scores or as fabricated data.

New model_boundary.assess keeps the real history bundle held. Required next work: confirmed target entry snapshot and its identity match; independently reviewed, timestamped feature extraction for the eight groups; trained/calibrated model and historical validation. Current two-profile/nine-run sample is a data plumbing validation, not enough for real performance claims.

synthetic_model_boundary_check.py supplies four fully fictional pre-race entries and all eight fabricated features to the unchanged existing model. Local check: four score rows and four combinations, real synthetic=False input rejected, no orders. This verifies synthetic entry-to-model interface only, NOT the real bundle-to-prediction conversion. No future actual race prediction was executed.

143 existing/unit tests pass, plus synthetic interface check. Independent review pending.
