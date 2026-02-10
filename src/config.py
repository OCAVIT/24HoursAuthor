"""Конфигурация приложения из переменных окружения."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Настройки приложения, загружаемые из .env."""

    # Автор24
    avtor24_email: str = ""
    avtor24_password: str = ""
    avtor24_base_url: str = "https://avtor24.ru"

    # OpenAI
    openai_api_key: str = ""
    openai_model_main: str = "gpt-4o"
    openai_model_fast: str = "gpt-4o-mini"

    # Антиплагиат
    etxt_api_key: str = ""
    textru_api_key: str = ""
    min_uniqueness: int = 50

    # Дашборд
    dashboard_username: str = "admin"
    dashboard_password_hash: str = ""
    dashboard_secret_key: str = "change-me-in-production"

    # База данных
    database_url: str = "sqlite+aiosqlite:///./avtor24.db"

    # Верификация по телефону (countrylock)
    avtor24_phone_last4: str = ""

    # Прокси
    proxy_ru: str = ""

    # Настройки бота
    max_concurrent_orders: int = 5
    auto_bid: bool = True
    bid_comment_template: str = "default"
    min_price_rub: int = 300
    max_price_rub: int = 50000
    scan_interval_seconds: int = 60
    speed_limit_min_delay: int = 30
    speed_limit_max_delay: int = 120

    # Stop-gate: запрещённые типы работ (через запятую)
    banned_work_types: str = ""

    @property
    def banned_work_types_list(self) -> list[str]:
        """Парсинг строки banned_work_types в список."""
        if not self.banned_work_types:
            return []
        return [t.strip() for t in self.banned_work_types.split(",") if t.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
