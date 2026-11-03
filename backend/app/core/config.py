"""Application settings, loaded from environment variables and the project-root .env file."""

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # "OLLAMA_MODEL=" with nothing after it means "use the default", not "empty".
        env_ignore_empty=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    cors_origins: str = "http://localhost:5173"

    database_url: str

    file_encryption_key: SecretStr

    storage_dir: Path = Path("storage")

    # Sessions end after this much inactivity, and in any case after the absolute limit.
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    # Failed logins in a row before the account is locked, and for how long.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # Failed sign-ins from one network address, across all accounts, before it is paused.
    login_ip_max_failures: int = 20
    login_ip_window_minutes: int = 15

    # Uploads
    max_upload_mb: int = 20
    max_pdf_pages: int = 100
    # Larger images are refused, which also blocks "decompression bomb" files.
    max_image_pixels: int = 40_000_000

    # Country assumed for phone numbers entered without a country code (ISO 3166 alpha-2).
    default_phone_region: str = "IN"

    # Text extraction. Tesseract language codes joined with "+", e.g. "eng+hin".
    ocr_languages: str = "eng"
    ocr_dpi: int = 300

    # LLM. "none" disables AI profiling; documents are still extracted and classified by rules.
    llm_provider: Literal["ollama", "anthropic", "none"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    # The "instruct" variant answers directly; the default qwen3-vl:2b tag is a "thinking"
    # variant that spends its output budget reasoning and ignores requests not to.
    ollama_model: str = "qwen3-vl:2b-instruct"
    llm_timeout_seconds: int = 300
    # Pages sent to the model as images, and their longest side in pixels.
    llm_profile_max_pages: int = 3
    llm_image_max_px: int = 1024
    # Ask the model to double-check COVERED decisions; a "no" sends the point to review.
    # Off by default: on the dev letters the local 2B model said "no" to correct matches
    # (2 of 9 COVERED points), wrongly holding 2 of 3 letters. Re-measure with a hosted model.
    match_model_check: bool = False

    # Patient messages. Empty company details block messages until they are set.
    company_name: str = ""
    support_email: str = ""
    support_phone: str = ""
    # Days the patient is given when the insurer's letter sets no deadline; 0 sends such
    # letters to staff instead.
    patient_response_days: int = 7

    # Delivery. "fake" is the local test outbox: nothing leaves this machine.
    whatsapp_provider: Literal["fake", "twilio", "meta"] = "fake"
    email_provider: Literal["fake", "resend"] = "fake"
    provider_timeout_seconds: int = 20

    # Twilio WhatsApp (sandbox or approved sender)
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    # The sender number, e.g. +14155238886 for the sandbox (without the "whatsapp:" prefix)
    twilio_whatsapp_from: str = ""

    # Meta WhatsApp Cloud API
    meta_wa_token: SecretStr = SecretStr("")
    meta_wa_phone_number_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    meta_verify_token: SecretStr = SecretStr("")
    meta_graph_version: str = "v22.0"

    # Resend email
    resend_api_key: SecretStr = SecretStr("")
    # Sender, e.g. "Claims Desk <claims@your-verified-domain.com>"
    email_from: str = ""
    resend_webhook_secret: SecretStr = SecretStr("")

    # Background worker
    worker_poll_seconds: float = 1.0
    # A running job not finished within this time is assumed lost and is retried.
    job_lease_minutes: int = 20

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Render and most hosts hand out postgres:// or postgresql:// URLs, which SQLAlchemy
        # maps to psycopg2. The installed driver is psycopg 3.
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+psycopg://" + value.removeprefix(scheme)
        return value

    @field_validator("file_encryption_key")
    @classmethod
    def _encryption_key_shape(cls, value: SecretStr) -> SecretStr:
        try:
            key = base64.urlsafe_b64decode(value.get_secret_value())
        except (binascii.Error, ValueError) as exc:
            raise ValueError("FILE_ENCRYPTION_KEY must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("FILE_ENCRYPTION_KEY must decode to 32 bytes; run `make env`")
        return value

    @field_validator("storage_dir")
    @classmethod
    def _resolve_storage_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT_DIR / value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cookie_secure(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the environment
