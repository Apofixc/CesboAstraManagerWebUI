"""
Модуль для управления конфигурацией приложения Astra Web-UI.

Предоставляет классы AppConfig и ConfigManager для определения,
валидации, загрузки и сохранения настроек приложения из JSON-файла.
Использует Pydantic для строгой типизации и автоматической валидации.
"""
import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles  # type: ignore
from pydantic import (BaseModel, Field, ValidationError, field_validator, # type: ignore
                      model_validator) # type: ignore

logger = logging.getLogger(__name__)

# Компилируем регулярные выражения один раз на уровне модуля
DOMAIN_REGEX = re.compile(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


class Instance(BaseModel):
    """
    Класс модели для отдельного серверного инстанса (адрес и порт)
    с автоматической валидацией.

    Ограничивает адрес как чистый хост без схемы/порта; порт в диапазоне 1-65535.
    """
    address: str = Field(..., description="Чистый хост/IP/домен сервера (без протокола/порта)")
    port: int = Field(..., ge=1, le=65535, description="Порт сервера (1-65535)")

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: str) -> str:
        """
        Валидирует значение адреса.

        Проверяет формат адреса (localhost/IP/домен) и отсутствие схем/портов
        для предотвращения SSRF-атак.

        Args:
            v (str): Значение адреса.

        Returns:
            str: Валидное значение адреса.

        Raises:
            ValueError: Если адрес некорректный (пустой, содержит схему, порт
                        или не соответствует ожидаемому формату).
        """
        if not v:
            raise ValueError("Address не может быть пустым")
        if ':' in v or v.lower().startswith(('http://', 'https://')):
            raise ValueError(f"Address должен быть чистым хостом: '{v}' "
                             "без протокола и порта")
        if v == 'localhost':
            pass
        else:
            try:
                ipaddress.ip_address(v) # Используем ipaddress для валидации IP
            except ValueError as exc:
                if DOMAIN_REGEX.match(v):  # Домен
                    pass
                else:
                    raise ValueError(f"Неверный address: '{v}' "
                                     "(ожидается localhost, IP или домен)") from exc
        return v


class AppConfig(BaseModel):
    """
    Класс основной модели конфигурации приложения с полями для хостов, портов,
    серверов, интервалов и таймаутов.

    Автоматическая валидация полей при создании или загрузке.
    """
    instance_host: str = Field("127.0.0.1",
                               description="Хост для инстансов (IP или домен)")
    start_port: int = Field(9200, ge=1, le=65535,
                            description="Начальный порт для сканирования (1-65535)")
    end_port: int = Field(9300, ge=1, le=65535,
                          description="Конечный порт для сканирования (1-65535)")
    servers: List[Instance] = Field(default_factory=list,
                                    description="Список серверов (объекты Instance)")
    check_interval: int = Field(300, gt=0,
                                description="Интервал проверки в секундах (больше 0)")
    debug: bool = Field(False, description="Режим отладки (True/False)")
    scan_timeout: int = Field(5, gt=0,
                              description="Таймаут сканирования в секундах (больше 0)")
    proxy_timeout: int = Field(15, gt=0,
                               description="Таймаут прокси в секундах (больше 0)")
    cache_ttl: int = Field(10, gt=0,
                           description="Время жизни кэша для инстансов в секундах (больше 0)")
    instance_alive_cache_ttl: int = Field(5, gt=0,
                                          description="Время жизни кэша для проверки доступности инстанса в секундах (больше 0)") # pylint: disable=C0301
    cached_instances: List[Dict[str, Any]] = Field(
        default_factory=list, description="Кэшированный список инстансов"
    )
    cache_timestamp: float = Field(0.0, description="Временная метка последнего обновления кэша")
    cors_allow_origin: str = Field("*", description="Значение заголовка Access-Control-Allow-Origin для CORS")
    debounce_save_delay: float = Field(5.0, gt=0,
                                       description="Задержка в секундах для отложенного сохранения конфигурации")

    @field_validator('instance_host')
    @classmethod
    def validate_host(cls, v: str) -> str:
        """
        Валидирует значение хоста.

        Проверяет формат хоста (localhost/IP/домен) и отсутствие схем/портов
        для предотвращения SSRF-атак.

        Args:
            v (str): Значение хоста.

        Returns:
            str: Валидное значение хоста.

        Raises:
            ValueError: Если хост некорректный (пустой, содержит схему, порт
                        или не соответствует ожидаемому формату).
        """
        if not v:
            raise ValueError("Хост не может быть пустым")
        if ':' in v or v.lower().startswith(('http://', 'https://')):
            raise ValueError(f"Хост должен быть чистым: '{v}' без протокола и порта")
        if v == 'localhost':
            pass
        else:
            try:
                ipaddress.ip_address(v) # Используем ipaddress для валидации IP
            except ValueError as exc:
                if DOMAIN_REGEX.match(v):  # Домен
                    pass
                else:
                    raise ValueError(f"Неверный хост: '{v}' (ожидается localhost, IP или домен)") from exc
        return v

    @field_validator('servers')
    @classmethod
    def validate_servers(cls, v: List[Union[Dict[str, Any], Instance]]) -> List[Instance]:
        """
        Валидирует список серверов.

        Фильтрует некорректные элементы `Instance` (quiet drop) с логированием
        и сохраняет только валидные объекты.

        Args:
            v (List[Union[Dict[str, Any], Instance]]): Список сырых данных или объектов Instance.

        Returns:
            List[Instance]: Отфильтрованный список валидных объектов Instance.
        """
        if not isinstance(v, list):
            raise ValueError("Servers должен быть списком")
        valid_servers: List[Instance] = []
        for item in v:
            try:
                if isinstance(item, dict):
                    server: Instance = Instance(**item)
                elif isinstance(item, Instance):
                    server = item
                else:
                    raise ValueError("Каждый элемент должен быть dict или Instance")
                valid_servers.append(server)
            except ValidationError as err:
                logger.warning("Некорректный сервер пропущен: %s", err)
        return valid_servers

    @model_validator(mode='after')
    def validate_ports(self) -> 'AppConfig':
        """
        Выполняет кросс-валидацию портов.

        Проверяет, что `start_port` меньше `end_port`.

        Returns:
            AppConfig: Объект модели (сам себя).

        Raises:
            ValueError: Если `start_port` больше или равен `end_port`.
        """
        if self.start_port >= self.end_port:
            raise ValueError(f"start_port ({self.start_port}) должен быть меньше "
                             f"end_port ({self.end_port})")
        return self


class ConfigManager:
    """
    Класс для управления загрузкой и валидацией конфигурации из JSON-файла
    с помощью Pydantic-моделей.

    При инициализации пытается загрузить конфигурацию из указанного файла.
    Если файл отсутствует, создаёт его с дефолтными значениями.
    """
    config_file_path: Path
    config: AppConfig

    def __init__(self, config_file_path: Optional[str] = 'config.json') -> None:
        """
        Инициализирует менеджер конфигурации.

        Если `config_file_path` равен `None`, используется значение по умолчанию 'config.json'.

        Args:
            config_file_path (Optional[str]): Путь к конфигурационному файлу.
                                              По умолчанию 'config.json'.
        """
        self.config_file_path = Path(config_file_path or 'config.json')
        self.config_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.config: AppConfig = AppConfig.model_validate({}) # Инициализируем с дефолтными значениями
    async def async_init(self) -> None:
        """
        Выполняет асинхронную инициализацию менеджера конфигурации.

        Загружает конфигурацию из файла после создания объекта.
        """
        self.config = await self._load_config()

    async def _load_config(self) -> AppConfig:
        """
        Загружает конфигурацию из JSON-файла.

        Метод проверяет наличие файла, при необходимости создаёт дефолтный,
        читает существующий и валидирует его через модель `AppConfig`.

        Returns:
            AppConfig: Валидный объект `AppConfig`, готовый к использованию.
        """
        if not self.config_file_path.exists():
            logger.info("Файл %s не найден. Создаём с дефолтными данными.",
                        self.config_file_path)
            default_config: AppConfig = AppConfig.model_validate({})
            async with aiofiles.open(self.config_file_path, mode='w',
                                     encoding='utf-8') as f:
                await f.write(default_config.model_dump_json(indent=4))
            return default_config

        try:
            async with aiofiles.open(self.config_file_path, mode='r',
                                     encoding='utf-8') as f:
                content = await f.read()
                data: Dict[str, Any] = json.loads(content)
            config: AppConfig = AppConfig.model_validate(data)
            return config
        except (json.JSONDecodeError, ValidationError) as err:
            logger.error("Ошибка загрузки/валидации: %s. Используем дефолты.", err)
            return AppConfig.model_validate({})

    def get_config(self) -> AppConfig:
        """
        Возвращает текущий загруженный объект конфигурации.

        Returns:
            AppConfig: Объект `AppConfig` с текущими настройками.
        """
        return self.config

    async def reload_config(self) -> None:
        """
        Перезагружает конфигурацию из файла.

        Текущие настройки в `self.config` будут перезаписаны новыми данными из файла
        после его валидации.
        """
        self.config = await self._load_config()

    async def save_config(self) -> None:
        """
        Сохраняет текущую конфигурацию в JSON-файл.

        Конфигурация сохраняется после валидации через модель `AppConfig`.

        Raises:
            IOError: При ошибке записи файла.
        """
        try:
            async with aiofiles.open(self.config_file_path, mode='w',
                                     encoding='utf-8') as f:
                await f.write(self.config.model_dump_json(indent=4))
        except IOError as err:
            logger.error("Ошибка сохранения: %s", err)
            raise
