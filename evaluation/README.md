# Evaluation inputs

Store consenting staged footage locally in `normal`, `staged_contact`, `staged_object_snatch`, `staged_fight`, `staged_fall`, `staged_restricted_entry`, `dark`, and `well_lit`. Media is excluded from Git. Include negatives such as hugs, handshakes, sports, object exchanges, dropped objects, crowded scenes, camera shake and abrupt lighting.

Annotate each video's duration and observable event intervals in seconds, following `example.json`. Record model predictions separately with event intervals and `detected_at`. Match uses temporal IoU and each annotation may match at most one prediction, so duplicate alerts are penalized.

The included JSONs are explicitly simulated schema examples. They demonstrate the harness, not model quality. Run with real predictions for each model/lighting/input condition and retain revision metadata. No false-alert/hour claim should be made without sufficient real negative-footage duration.
