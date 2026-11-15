from pathlib import Path
import secrets
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    data_dir: Path = Path('data')
    app_token: str = ''
    public_url: str = 'http://localhost:8000'
    streaming_model: str = 'moss'
    moss_ws_url: str = 'ws://127.0.0.1:8001/v1/realtime'
    moss_service_command: str = ''
    model_revision: str = 'unconfigured'
    observation_verifier_model: str = 'none'
    verifier_checkpoint: str = ''
    verifier_adapter: str = ''
    verifier_research_mode: bool = False
    verification_interval_seconds: float = Field(4,ge=2,le=30)
    verification_frame_count: int = Field(4,ge=4,le=8)
    max_inference_lag_seconds: float = Field(60,ge=10,le=120)
    alert_agreement_seconds: float = Field(12,ge=2,le=30)
    enhancement_model: str = 'bypass'
    fastllve_checkpoint: str = ''
    pre_event_seconds: float = Field(10, ge=0, le=60)
    post_event_seconds: float = Field(10, ge=0, le=60)
    object_post_event_seconds: float = Field(30,ge=0,le=60)
    sample_fps: float = Field(2, ge=0.2, le=10)
    capture_fps: float = Field(12, ge=1, le=15)
    dark_luminance_threshold: float = 45
    light_luminance_threshold: float = 70
    alert_confidence_threshold: float = 0.75
    alert_cooldown_seconds: float = 20
    retention_hours: float = Field(24, gt=0)
    enable_relay_vsr: bool = False
    relay_max_input_dimension: int = Field(480,ge=192,le=1280)
    enable_relift: bool = False
    relift_python: str = ''
    relift_frame_limit: int = Field(84, ge=2, le=84)
    enable_tracking: bool = False
    moss_spatial_output: bool = False
    enable_bvimamba: bool = False
    # Detector cascade (STREAMING_MODEL=cascade); see docs/overview/cascade.md.
    detector_weights: str = 'yolo11n.pt'
    cascade_pose: bool = True
    pose_weights: str = 'yolo11n-pose.pt'
    person_down_seconds: float = Field(10, ge=2, le=120)
    rack_dwell_seconds: float = Field(45, ge=5, le=600)
    rack_zones: str = ''
    cascade_cooldown_seconds: float = Field(30, ge=0, le=600)
    # Dispatch agent (apps/api/dispatch_agent.py): recommends units; a person approves.
    dispatch_agent_model: str = 'claude-opus-5-5'
    anthropic_api_key: str = ''
    # Adversarial debate (apps/api/debate.py): 'auto' = claude if ANTHROPIC_API_KEY is set, else rules.
    debate_gate: str = 'auto'  # 'auto' | 'rules' | 'claude' | 'jev'
    debate_model: str = 'claude-haiku-5-5'
    enable_clip_review: bool = False
    allow_rtsp: bool = False
    max_upload_mb: int = 500
    supabase_url: str = ''
    supabase_secret_key: SecretStr = SecretStr('')
    supabase_bucket: str = 'safesc-incidents'
    archive_max_mb: int = Field(800,ge=1,le=900)

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
token_file = settings.data_dir / 'app_token'
if not settings.app_token:
    if not token_file.exists():
        with token_file.open('x') as f:
            f.write(secrets.token_urlsafe(32))
        token_file.chmod(0o600)
    settings.app_token = token_file.read_text().strip()
