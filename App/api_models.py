"""
Модуль для определения Pydantic-моделей, используемых для валидации
входящих API-запросов в приложении Astra Web-UI.
"""
from typing import Optional, Any
from pydantic import BaseModel, Field

class AstraAddrRequest(BaseModel):
    """
    Модель для запросов, требующих указания адреса Astra инстанса.
    """
    astra_addr: str = Field(..., description="Адрес Astra инстанса в формате 'хост:порт'")

class CreateChannelRequest(AstraAddrRequest):
    """
    Модель для запроса создания канала.
    """
    name: str = Field(..., description="Имя канала")
    url: str = Field(..., description="URL источника канала")
    # Добавьте другие поля, если они требуются для создания канала

class ControlStreamRequest(AstraAddrRequest):
    """
    Модель для запросов управления потоком (kill_stream, kill_channel, kill_monitor).
    """
    id: Optional[str] = Field(None, description="ID потока/канала/монитора для управления")
    # Добавьте другие поля, если они требуются для управления потоком

class GetMonitorDataRequest(AstraAddrRequest):
    """
    Модель для запроса получения данных монитора.
    """
    id: Optional[str] = Field(None, description="ID монитора для получения данных")

class GetAdapterDataRequest(AstraAddrRequest):
    """
    Модель для запроса получения данных адаптера.
    """
    id: Optional[str] = Field(None, description="ID адаптера для получения данных")

class GetPsiChannelRequest(AstraAddrRequest):
    """
    Модель для запроса получения PSI канала.
    """
    id: Optional[str] = Field(None, description="ID PSI канала для получения данных")

class ErrorResponse(BaseModel):
    """
    Стандартизированная модель для ответов об ошибках API.
    """
    error: str = Field(..., description="Краткое описание ошибки")
    message: Optional[str] = Field(None, description="Подробное сообщение об ошибке")
    details: Optional[Any] = Field(None, description="Дополнительные детали ошибки (например, ошибки валидации)")

# Добавьте другие модели по мере необходимости для других эндпоинтов
