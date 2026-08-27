from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "139云应用平台自动配链接编排层"
    app_env: str = "dev"
    database_path: Path = Path("./data/orchestrator.sqlite3")
    automation_executor_path: Path = Path("app/executor")
    static_files_dir: Path = Path("./data/files")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def resolve_from_project(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (Path.cwd() / path).resolve()


@lru_cache
def get_settings() -> Settings:
    current = Settings()
    current.database_path = current.resolve_from_project(current.database_path)
    current.automation_executor_path = current.resolve_from_project(current.automation_executor_path)
    current.static_files_dir = current.resolve_from_project(current.static_files_dir)
    return current


settings = get_settings()



