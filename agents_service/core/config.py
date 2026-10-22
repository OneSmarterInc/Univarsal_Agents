"""Every setting in one place.

Values come from environment variables with the ``AGENTS_`` prefix, or from a
``.env`` file in the folder the service starts in. Environment variables win.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Provider = Literal['ollama', 'openai', 'anthropic']

# Used for any model limit not set. Qwen on Ollama has thinking switched off;
# other servers get no reasoning_effort unless it is set.
PROVIDER_DEFAULTS = {
    'ollama': {'context_tokens': 16384, 'output_tokens': 2048, 'reasoning_effort': 'none'},
    'openai': {'context_tokens': 16384, 'output_tokens': 2048, 'reasoning_effort': ''},
    'anthropic': {'context_tokens': 131072, 'output_tokens': 8000, 'reasoning_effort': ''},
}
# Settings that belong to one provider: an override that changes the provider
# (or the server) does not take the global value, only its own or the default.
PROVIDER_BOUND = ('api_key', 'context_tokens', 'output_tokens', 'reasoning_effort')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='AGENTS_', env_file='.env', env_file_encoding='utf-8', extra='ignore'
    )

    # Storage of jobs, keys and usage. SQLite on a laptop, PostgreSQL on a server.
    database_url: str = 'sqlite:///data/agents.db'
    data_dir: Path = Path('data')

    # Secret mixed into every key hash. At least 32 random characters.
    key_pepper: str = Field(min_length=32)

    # Where uploaded files wait for their job: the local disk or S3.
    storage: Literal['local', 's3'] = 'local'
    s3_bucket: str = ''
    s3_prefix: str = 'uploads/'
    s3_region: str = ''
    s3_endpoint_url: str = ''

    # Agents switched on. Each name is a folder in agents_service/agents/.
    enabled_agents: Annotated[list[str], NoDecode] = ['cv', 'linkedin']

    # The model every agent uses unless an override below says otherwise.
    # ollama and openai: an OpenAI-compatible server (Ollama, vLLM, a hosted API).
    # anthropic: the Anthropic API with an API key.
    model_provider: Provider = 'ollama'
    model_base_url: str = 'http://127.0.0.1:11434/v1'
    model_name: str = 'kormic-local'
    model_api_key: str = ''
    model_structured_mode: Literal['json_schema', 'tools'] = 'json_schema'
    # Empty = the provider's default (see PROVIDER_DEFAULTS).
    model_reasoning_effort: Literal['', 'none', 'low', 'medium', 'high'] = ''
    model_context_tokens: int | None = None
    model_output_tokens: int | None = None
    model_allow_http: bool = False
    # Longest wait for one model answer.
    model_request_seconds: int = 120
    # Put the document text before the question, so Ollama can reuse it
    # across the questions about one piece of text (faster on a CPU).
    model_text_first: bool = True
    # Per agent, any of the model_* values above, for example
    # AGENTS_MODEL_OVERRIDES='{"linkedin": {"provider": "anthropic", "name": "claude-...", "api_key": "..."}}'
    # An override that changes the provider or server needs its own api_key and
    # gets that provider's default limits unless it sets them.
    model_overrides: dict[str, dict] = {}

    # Reading.
    tesseract_cmd: str = ''
    # Read every file in a separate, limited process (recommended).
    isolate_parsing: bool = True
    file_seconds: int = Field(default=900, ge=5)
    cv_chunk_chars: int = 3000
    linkedin_max_files: int = Field(default=5, ge=1, le=10)

    # Limits per key and per project.
    requests_per_minute: int = 60
    # Checking a job's state (polling) has its own, higher limit.
    reads_per_minute: int = 600
    jobs_per_minute: int = 10
    max_waiting_per_project: int = 50
    max_waiting_total: int = 500

    # The job line. A worker renews its borrow every 10 seconds, so a borrow
    # lasts at least 30; results are kept at least as long as jobs may wait.
    lease_seconds: int = Field(default=120, ge=30)
    max_attempts: int = Field(default=2, ge=1, le=10)
    queue_expiry_seconds: int = Field(default=1800, ge=60)
    result_retention_seconds: int = Field(default=7 * 86400, ge=3600)
    poll_seconds: float = Field(default=2.0, gt=0)

    @field_validator('enabled_agents', mode='before')
    @classmethod
    def split_agents(cls, value):
        if isinstance(value, str):
            return [part.strip() for part in value.split(',') if part.strip()]
        return value

    @model_validator(mode='after')
    def check(self):
        if self.key_pepper.upper().startswith('REPLACE'):
            raise ValueError('Set AGENTS_KEY_PEPPER to a random value before starting.')
        if self.storage == 's3' and not self.s3_bucket:
            raise ValueError('AGENTS_S3_BUCKET is required when AGENTS_STORAGE=s3.')
        if not self.s3_prefix.endswith('/') or self.s3_prefix.strip('/') == '':
            # The service deletes old files under this prefix; it must be its own folder.
            raise ValueError('AGENTS_S3_PREFIX must be a folder of its own, like "uploads/".')
        if self.result_retention_seconds <= self.queue_expiry_seconds:
            raise ValueError(
                'AGENTS_RESULT_RETENTION_SECONDS must be longer than AGENTS_QUEUE_EXPIRY_SECONDS.'
            )
        return self

    def model_for(self, agent):
        """The model settings for one agent (provider, address, name, limits), after its override."""
        values = {
            'provider': self.model_provider,
            'base_url': self.model_base_url.rstrip('/'),
            'name': self.model_name,
            'api_key': self.model_api_key,
            'structured_mode': self.model_structured_mode,
            'reasoning_effort': self.model_reasoning_effort,
            'context_tokens': self.model_context_tokens,
            'output_tokens': self.model_output_tokens,
            'allow_http': self.model_allow_http,
            'request_seconds': self.model_request_seconds,
            'text_first': self.model_text_first,
        }
        override = self.model_overrides.get(agent, {})
        unknown = set(override) - set(values)
        if unknown:
            raise ValueError(f'Unknown model override for {agent}: {", ".join(sorted(unknown))}')
        moved = override.get('provider', values['provider']) != values['provider'] or (
            'base_url' in override and str(override['base_url']).rstrip('/') != values['base_url']
        )
        if moved:
            # A key or limit set for the global model never goes to another provider or server.
            for name in PROVIDER_BOUND:
                values[name] = '' if name == 'api_key' else None
        values.update(override)
        defaults = PROVIDER_DEFAULTS.get(values['provider'], PROVIDER_DEFAULTS['openai'])
        for name, default in defaults.items():
            if values.get(name) in (None, ''):
                values[name] = default
        values['base_url'] = str(values['base_url']).rstrip('/')
        return values


@lru_cache
def get_settings():
    return Settings()
