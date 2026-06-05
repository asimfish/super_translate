"""Application configuration."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Paper China"
    debug: bool = False

    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    data_dir: Path = Path("data")
    papers_dir: Path = Path("data/papers")
    translations_dir: Path = Path("data/translations")
    db_path: Path = Path("data/paper_china.db")

    translation_backend: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-pro"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.base_dir / self.db_path}"

    @property
    def papers_path(self) -> Path:
        return self.base_dir / self.papers_dir

    @property
    def translations_path(self) -> Path:
        return self.base_dir / self.translations_dir

    model_config = {"env_prefix": "PAPER_CHINA_", "env_file": ".env"}


settings = Settings()


def ensure_dirs() -> None:
    settings.papers_path.mkdir(parents=True, exist_ok=True)
    settings.translations_path.mkdir(parents=True, exist_ok=True)
    (settings.base_dir / settings.data_dir).mkdir(parents=True, exist_ok=True)
