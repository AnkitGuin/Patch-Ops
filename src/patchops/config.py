from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PATCHOPS_", env_file=".env", env_ignore_empty=True, extra="ignore"
    )
    database_url: str = "sqlite:///./var/patchops.db"
    data_root: Path = Path("var")
    allowed_roots: list[Path] = Field(default_factory=lambda: [Path.cwd()])
    git_hosts: list[str] = Field(
        default_factory=lambda: ["github.com", "gitlab.com", "bitbucket.org"]
    )
    api_token: SecretStr | None = None
    sandbox_image: str = "patchops-sandbox:0.1.0"
    install_network: str = "none"
    command_seconds: int = Field(default=180, ge=1, le=900)
    job_seconds: int = Field(default=900, ge=15, le=7200)
    sandbox_memory: str = Field(default="1g", pattern=r"^[1-9]\d*[kmg]$")
    sandbox_disk: str = Field(default="768m", pattern=r"^[1-9]\d*[kmg]$")
    sandbox_cpus: float = Field(default=1.5, gt=0, le=16)
    sandbox_pids: int = Field(default=128, ge=16, le=512)
    max_repair_attempts: int = Field(default=3, ge=0, le=3)
    max_semantic_edits: int = Field(default=8, ge=1, le=30)
    max_files: int = Field(default=10000, ge=1, le=20000)
    max_file_mb: int = Field(default=5, ge=1, le=10)
    max_repo_mb: int = Field(default=100, ge=1, le=200)
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_timeout: int = Field(default=35, ge=1, le=90)
    llm_retries: int = Field(default=1, ge=0, le=2)
    llm_token_budget: int = Field(default=16000, ge=1000, le=100000)
    github_token: SecretStr | None = None
