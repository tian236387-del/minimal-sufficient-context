from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent

DEFAULT_SYSTEM_PROMPT = (
    "你是 Minimal Sufficient Context 原型中的本地助手。"
    "严格根据当前活动分支可见的上下文回答。"
    "不要假设或引用不在当前分支中的 sibling branch 信息。"
)

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path = BACKEND_DIR / "msc_chat.db"
    migrations_path: Path = BACKEND_DIR / "migrations"
    backup_path: Path = BACKEND_DIR / "backups"
    backup_before_migrate: bool = True
    ollama_base_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen3:4b"
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    provider_request_timeout_seconds: float = 300.0
    provider_models_timeout_seconds: float = 10.0
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS

    @classmethod
    def from_env(cls) -> "Settings":
        cors_value = os.getenv("MSC_CORS_ORIGINS")
        cors_origins = (
            tuple(item.strip() for item in cors_value.split(",") if item.strip())
            if cors_value
            else DEFAULT_CORS_ORIGINS
        )
        return cls(
            database_path=_env_path("MSC_DB_PATH", BACKEND_DIR / "msc_chat.db"),
            migrations_path=_env_path(
                "MSC_MIGRATIONS_PATH", BACKEND_DIR / "migrations"
            ),
            backup_path=_env_path("MSC_BACKUP_PATH", BACKEND_DIR / "backups"),
            backup_before_migrate=_env_bool("MSC_BACKUP_BEFORE_MIGRATE", True),
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
            ).rstrip("/"),
            default_model=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
            default_system_prompt=os.getenv(
                "MSC_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT
            ),
            provider_request_timeout_seconds=float(
                os.getenv("MSC_PROVIDER_TIMEOUT_SECONDS", "300")
            ),
            provider_models_timeout_seconds=float(
                os.getenv("MSC_PROVIDER_MODELS_TIMEOUT_SECONDS", "10")
            ),
            cors_origins=cors_origins,
        )

