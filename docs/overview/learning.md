# Reviewed video learning

The **Learning repository** workspace contains original videos, permission history, append-only action annotations, immutable dataset snapshots, and actual model experiment results. Its default filter shows the videos used by the latest completed training run. Validation and held-out test examples are separately marked **not trained**. The video player seeks to the annotated interval; the four original sampled frames actually supplied to the model are shown underneath.

## Review-to-dataset flow

An incident confirmation automatically retains the original review window only when its source has explicit training permission. Confirmation alone does not create a training label. Reviewers must identify a visible action and its start/end times. External context, uncertain visibility, unknown permissions, and simulated fixtures cannot produce approved training examples. Reviewed ordinary activity supplies negative examples. Missed events can be uploaded directly.

Permissions and annotations are append-only; corrections supersede older labels. Source permission revocation removes retained examples from future eligibility. Each original recording and its derived clips stay in one train/validation/test split. Snapshots preserve hashes, permissions, label revisions, and provenance. Operational video retention remains separate from 30-day retained learning copies. Active training records protect required learning assets from expiry. Model updates are explicitly launched; no self-label training or silent automatic weight changes occur.

This branch uses the existing authenticated reviewer workspace. Separate student/reviewer accounts and student notifications remain future work. Research annotations are attributed to their authors and assistant visual curation, not represented as USC-official confirmations.

## First actual experiment

The source footage is the official [UCF-Crime archive](https://www.crcv.ucf.edu/projects/real-world/), paired with [UCA timestamped annotations](https://github.com/Xuange923/Surveillance-Video-Understanding). Annotation revision: `e478cb5b2277c6b7cf6a1dc4691021592003e216`. Only selected ZIP members were fetched using HTTP Range; the full roughly 103 GB archive was not downloaded. These are academic research materials, not verified legal outcomes or production-cleared video assets.

49 source filenames were inspected; SHA-256 deduplication collapsed one duplicate recording, leaving 48 retained videos. Nine unique ambiguous or off-target examples remain pending. The 39 eligible examples preserve the authors' original splits: **19 training, 8 validation, 12 test**. See [exact recording IDs, selected intervals and hashes](../../evaluation/learning-pilot.json). Some ordinary windows occur before an event in a video with an anomalous category; those selected windows are explicitly labeled as ordinary activity. Broad video categories are never blindly applied to every frame.

The target is `Qwen/Qwen3-VL-4B-Instruct`, revision `ebb281ec70b05090aa6165b016eac8ec08e71b17`. MOSS remains the unchanged streaming observer. A real LoRA adapter trained the language attention query/value projections: rank 8, alpha 16, learning rate 0.0001, 60 optimizer updates, two microbatches per update, seed 42. Inputs contain four timestamped source frames and the output schema, without video IDs, dataset categories, or source captions. Targets describe physical contact, object removal, or ordinary activity; they do not establish ownership, intent, or guilt. Model confidence values are uncalibrated.

On the 12 prelocalized test clips, the unchanged baseline produced valid review alerts on 1/7 positives; the candidate produced them on 7/7. It falsely flagged 2/5 ordinary clips, compared with 0/5 for the baseline, and classified the action correctly on 9/12 clips. The baseline rejected five outputs under strict validation, so the improvement includes structured-output compliance and must not be presented solely as improved visual understanding. Validation contained only eight clips. Full measured results are in [the experiment report](../../evaluation/learning-pilot-results.json).

**Automatic promotion failed** because normal-clip false positives increased. The owner explicitly approved this higher-recall candidate for the staged hackathon research demo. A hash-bound research approval is required to enable that artifact; it does not turn the failed automatic gate into a pass. The UI and provenance label research mode, and human review remains required. These results do not measure live temporal-gate recall, false alerts per hour, camera capacity, or production safety accuracy. Foundation-model pretraining overlap is unknown. Additional representative footage and genuinely independent evaluation are necessary before campus deployment.

## Reproduce and inspect

```bash
uv run python -m scripts.data.prepare_learning_seed
uv run python -m scripts.data.curate_learning_seed
```

Read the generated `data/learning-research/curation.json` for the snapshot path, then export the exact input bundle:

```bash
uv run python -m scripts.prepare_learning_training PATH_TO_SNAPSHOT
```

On an isolated Linux/CUDA worker, `scripts/setup_learning_training.sh` installs the pinned training environment. Run `python -m scripts.train_learning` from that environment. It evaluates the base model, performs actual gradient updates using only training records, and evaluates the candidate. Training does not activate the model. The adapter, original footage, frame bundles, approval record, and full local experiment data remain under ignored `data/`; no weights or videos are pushed to GitHub.

The experiment used a temporary A40 to preserve the working live GPU. It was terminated after the trained artifact and reports were backed up. Serverless remains an alternative for intermittent future jobs; it is not assumed cheaper than a short-lived Pod.
