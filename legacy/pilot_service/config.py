from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTS_", extra="ignore")
    database_url: str = "sqlite:///data/service.db"
    data_dir: Path = Path("data")
    key_pepper: str = Field(min_length=32)
    token_encryption_key: str
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_allowed_host: str = "127.0.0.1"
    ollama_model: str = "univarsal-qwen3-1.7b"
    ollama_digest: str = ""
    model_artifact_sha256: str = ""
    inference_timeout: float = 180
    context_tokens: int = 8192
    output_tokens: int = 2048
    job_timeout: int = 1800
    daily_jobs: int = 100
    tenant_pending: int = 20
    global_pending: int = 100
    retention_seconds: int = 7 * 86400
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""

    @model_validator(mode="after")
    def validate_config(self):
        from cryptography.fernet import Fernet

        Fernet(self.token_encryption_key.encode())
        if self.key_pepper.startswith("REPLACE"):
            raise ValueError("Replace placeholder secrets before starting")
        parsed = urlsplit(self.ollama_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != self.ollama_allowed_host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama must use the operator-approved private origin")
        if "://" in self.ollama_model or "cloud" in self.ollama_model.lower():
            raise ValueError("Only an approved local model alias is allowed")
        if self.context_tokens < 4096 or not 128 <= self.output_tokens < self.context_tokens:
            raise ValueError("Invalid inference token budget")
        return self
