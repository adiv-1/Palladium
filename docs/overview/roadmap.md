# Planned next phase

These are design decisions discussed after the implemented prototype. They are **not current features**.

1. Add reviewer and student accounts. Only reviewer-approved summaries should reach students through an in-app feed and opt-in browser notifications; email/SMS can be optional adapters later.
2. Demonstrate two simultaneous phone cameras, with independent timestamps, buffers, tracks, and model sessions. Benchmark one-GPU capacity honestly; use temporary additional compute if necessary rather than representing unsupported capacity as working.
3. Implemented in the reviewed-learning branch: a reviewed learning repository. Confirmation should retain examples automatically only when source permissions allow training and labels are visually supported. Include reviewed negatives and missed-event uploads.
4. Add workflow agents for intake, annotation drafts, dataset curation, evaluation, and notifications. Agents propose work; human decisions establish approved labels. Separate original clips, annotation revisions, dataset versions, and model releases.
5. A first Qwen visual-verifier LoRA research pilot is trained and evaluated; expand the dataset and independent evaluation before campus deployment. Serverless or a temporary GPU worker can run bounded training without interrupting monitoring. Storing examples alone does not update model weights.
6. Add leakage-aware forecasting of reported incident counts. Current historical map data lacks historical as-of versions and pedestrian exposure, and reporting-system changes must be accounted for.

Optimize candidate generation for high recall and measure missed events alongside reviewer workload. Do not claim zero false negatives, unmeasured camera capacity, savings over an unmatched competitor offering, or cost-free on-prem operation.
