import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Union, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator # type: ignore
import re

logger = logging.getLogger(__name__)

class Instance(BaseModel):
    """
    Класс модели для отдельного серверного инстанса (адрес и порт) с автоматической валидацией.
    
    Ограничивает адрес как чистый хост без схемы/порта; порт в диапазоне 1-65535.
    """
    address: str = Field(..., description="Чистый хост/IP/домен сервера (без протокола/порта)")
    port: int = Field(..., ge=1, le=65535, description="Порт сервера (1-65535)")

    @field_validator('address')
    @classmethod
    def validate_address(cls, v: str) -> str:
        """
        Валидирует значение адреса: проверяет формат (localhost/IP/домен), отсутствие схем и портов (фильтр SSRF).

        Args:
            v: Значение адреса (строка).

        Returns:
            Валидное значение адреса (строка).

        Raises:
            ValueError: Если адрес некорректный (пустой, содержит схему, порт или не соответствует формату).
        """
        if not v:
            raise ValueError("Address не может быть пустым")
        if ':' in v or v.lower().startswith(('http://', 'https://')):
            raise ValueError(f"Address должен быть чистым хостом: '{v}' без протокола и порта")
        if v == 'localhost':
            pass
        elif re.match(r'^\d+\.\d+\.\d+\.\d+$', v):  # IP-адрес
            octets: List[str] = v.split('.')
            if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
                raise ValueError(f"Неверный IP: {v}")
        elif re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):  # Домен
            pass
        else:
            raise ValueError(f"Неверный address: '{v}' (ожидается localhost, IP или домен)")
        return v

class AppConfig(BaseModel):
    """
    Класс основной модели конфигурации приложения с полями для хостов, портов, серверов, интервалов и таймаутов.
    
    Автоматическая валидация полей при создании или загрузке.
    """
    instance_host: str = Field("127.0.0.1", description="Хост для инстансов (IP или домен)")
    start_port: int = Field(9200, ge=1, le=65535, description="Начальный порт для сканирования (1-65535)")
    end_port: int = Field(9300, ge=1, le=65535, description="Конечный порт для сканирования (1-65535)")
    servers: List[Instance] = Field(default_factory=list, description="Список серверов (объекты Instance)")
    check_interval: int = Field(300, gt=0, description="Интервал проверки в секундах (больше 0)")
    server_host: str = Field("127.0.0.1", description="Хост основного сервера (IP или домен)")
    server_port: int = Field(5000, ge=1, le=65535, description="Порт основного сервера (1-65535)")
    debug: bool = Field(False, description="Режим отладки (True/False)")
    scan_timeout: int = Field(5, gt=0, description="Таймаут сканирования в секундах (больше 0)")
    proxy_timeout: int = Field(15, gt=0, description="Таймаут прокси в секундах (больше 0)")

    @field_validator('instance_host', 'server_host')
    @classmethod
    def validate_host(cls, v: str) -> str:
        """
        Валидирует значение хоста: проверяет формат (localhost/IP/домен), отсутствие схем и портов (фильтр SSRF).

        Args:
            v: Значение хоста (строка).

        Returns:
            Валидное значение хоста (строка).

        Raises:
            ValueError: Если хост некорректный (пустой, содержит схему, порт или не соответствует формату).
        """
        if not v:
            raise ValueError("Хост не может быть пустым")
        if ':' in v or v.lower().startswith(('http://', 'https://')):
            raise ValueError(f"Хост должен быть чистым: '{v}' без протокола и порта")
        if v == 'localhost':
            pass
        elif re.match(r'^\d+\.\d+\.\d+\.\d+$', v):  # IP-адрес
            octets: List[str] = v.split('.')
            if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
                raise ValueError(f"Неверный IP: {v}")
        elif re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):  # Домен
            pass
        else:
            raise ValueError(f"Неверный хост: '{v}' (ожидается localhost, IP или домен)")
        return v

    @field_validator('servers')
    @classmethod
    def validate_servers(cls, v: List[Union[Dict[str, Any], Instance]]) -> List[Instance]:
        """
        Валидирует список серверов: фильтрует некорректные элементы Instance (quiet drop) с логированием, сохраняет валидные.

        Args:
            v: Список сырых данных или объектов Instance.

        Returns:
            Фильтрованный список валидных объектов Instance.
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
            except ValidationError as e:
                logger.warning(f"Некорректный сервер пропущен: {e}")
        return valid_servers

    @model_validator(mode='after')
    def validate_ports(self) -> 'AppConfig':
        """
        Производит кросс-валидацию портов: проверяет, что start_port < end_port.

        Returns:
            Объект модели (сам себя).

        Raises:
            ValueError: Если start_port >= end_port.
        """
        if self.start_port >= self.end_port:
            raise ValueError(f"start_port ({self.start_port}) должен быть меньше end_port ({self.end_port})")
        return self

class ConfigManager:
    """
    Класс для управления загрузкой и валидацией конфигурации из JSON-файла с помощью Pydantic-моделей.
    
    При инициализации пытается загрузить конфигурацию из указанного файла. Если файл отсутствует, создаёт его с дефолтными значениями.
    """
    config_file_path: Path
    config: AppConfig

    def __init__(self, config_file_path: Optional[str] = 'config.json') -> None:
        """
        Инициализирует менеджер конфигурации и загружает настройки.
        
        Если config_file_path None, используется дефолт 'config.json'.

        Args:
            config_file_path: Путь к конфигурационному файлу (по умолчанию 'config.json'; можно None для дефолта).
        """
        if config_file_path is None:
            actual_config_path: str = 'config.json'
        else:
            actual_config_path = config_file_path
        self.config_file_path = Path(actual_config_path)
        self.config_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()

    def _load_config(self) -> AppConfig:
        """
        Загружает конфигурацию из JSON-файла, валидирует через Pydantic, создаёт файл при отсутствии.
        
        Метод проверяет наличие файла, при необходимости создаёт дефолтный,
        читает существующий и валидирует его через модель AppConfig.

        Returns:
            Валидный объект AppConfig, готовый к использованию.
        """
        if not self.config_file_path.exists():
            logger.info(f"Файл {self.config_file_path} не найден. Создаём с дефолтными данными.")
            default_config: AppConfig = AppConfig()
            with open(self.config_file_path, 'w', encoding='utf-8') as f:
                f.write(default_config.model_dump_json(indent=4))
            return default_config

        try:
            with open(self.config_file_path, 'r', encoding='utf-8') as f:
                data: Dict[str, Any] = json.load(f)
            config: AppConfig = AppConfig(**data)
            return config
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Ошибка загрузки/валидации: {e}. Используем дефолты.")
            return AppConfig()

    def get_config(self) -> AppConfig:
        """
        Возвращает текущий загруженный объект конфигурации.

        Returns:
            Объект AppConfig с текущими настройками.
        """
        return self.config

    def reload_config(self) -> None:
        """
        Перезагружает конфигурацию из файла с валидацией.
        
        Текущие настройки в self.config будут перезаписаны новыми данными из файла.
        """
        self.config = self._load_config()

    def save_config(self) -> None:
        """
        Сохраняет текущую конфигурацию в JSON-файл (после валидации через модель).

        Raises:
            IOError: При ошибке записи файла.
        """
        try:
            with open(self.config_file_path, 'w', encoding='utf-8') as f:
                f.write(self.config.model_dump_json(indent=4))
        except IOError as e:
            logger.error(f"Ошибка сохранения: {e}")
            raise