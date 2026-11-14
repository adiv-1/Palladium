# Architecture

The [full text diagram](../architecture.txt) describes the implemented single-GPU system and phone protocol.

FastAPI owns source admission, timestamp validation, rolling original buffers, incident metadata, hashing, persistent jobs, and WebSocket telemetry. React/Vite provides capture, live monitoring, evidence review, historical activity mapping, and a Three.js trajectory viewer. SQLite stores sources, jobs, incidents, and reviewer actions.

CPU phone intake acknowledges received frames independently of GPU inference. A bounded latest-frame queue exposes skipped samples rather than accumulating unbounded latency. HTTPS and scoped expiring pairing tokens allow a phone to connect over Wi-Fi or cellular. The application currently admits one active video source.

MOSS-VL NF4 is the streaming observer. Qwen3-VL-4B checks original evidence frames and emits constrained JSON, which is validated against actual frame IDs, timestamps, and temporal alert rules. Native MOSS JSON experiments failed strict validation; raw narrative alone never creates an incident.

FastLLVE runs when lighting requires enhancement. YOLO11n and ByteTrack provide temporary anonymous person tracks. Once the live source closes, resident models unload and a persistent queue serializes RelayVSR, ReLift, and optional BVI-Mamba subprocesses. Derived artifacts have visible labels, hashes, and transformation logs.

The activity map is independent of video judgments. Its importer builds a minimized local LAPD snapshot, and authenticated endpoints serve date-filtered points and source-grounded summaries. Historical patterns never increase a video's evidence score. Forecasting remains future work.
