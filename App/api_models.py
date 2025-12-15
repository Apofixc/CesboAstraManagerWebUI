"""
Модуль для определения Pydantic-моделей, используемых для валидации
входящих API-запросов в приложении Astra Web-UI.
"""
from typing import Optional, Any, Dict, List
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
    channel: str = Field(..., description="Имя канала для управления")
    reboot: Optional[bool] = Field(False, description="Перезагрузить после убийства")
    delay: Optional[int] = Field(30, gt=0, description="Задержка перед перезагрузкой в секундах")

class MonitorStatus(BaseModel):
    """
    Модель для статуса монитора канала (ответ от Astra).
    """
    type: str
    server: str
    channel: str
    output: str
    stream: str
    format: str
    addr: str
    ready: bool
    scrambled: bool
    bitrate: int
    cc_errors: int
    pes_errors: int

class PsiData(BaseModel):
    """
    Модель для PSI данных (ответ от Astra).
    """
    # Это может быть сложная структура, пока оставим как Any
    psi: Any

class ChannelListItem(BaseModel):
    """
    Модель для элемента списка каналов (ответ от Astra).
    """
    name: str
    addr: str

class ChannelListResponse(BaseModel):
    """
    Модель для ответа со списком каналов (ответ от Astra).
    """
    # Ключи могут быть динамическими, например "channel_1", "channel_2"
    # Поэтому используем Dict[str, ChannelListItem]
    __root__: Dict[str, ChannelListItem]

class MonitorListItem(BaseModel):
    """
    Модель для элемента списка мониторов (ответ от Astra).
    """
    __root__: str # Имя монитора

class MonitorListResponse(BaseModel):
    """
    Модель для ответа со списком мониторов (ответ от Astra).
    """
    __root__: Dict[str, MonitorListItem]

class AdapterStatus(BaseModel):
    """
    Модель для статуса DVB-адаптера (ответ от Astra).
    """
    type: str
    server: str
    format: str
    modulation: str
    source: str
    name_adapter: str
    status: int
    signal: int
    snr: int
    ber: int
    unc: int

class AdapterListItem(BaseModel):
    """
    Модель для элемента списка адаптеров (ответ от Astra).
    """
    __root__: str # Имя адаптера

class AdapterListResponse(BaseModel):
    """
    Модель для ответа со списком адаптеров (ответ от Astra).
    """
    __root__: Dict[str, AdapterListItem]

class AstraHealthResponse(BaseModel):
    """
    Модель для ответа Health Check от Astra.
    """
    addr: str
    port: int
    version: str

class GetMonitorDataRequest(AstraAddrRequest):
    """
    Модель для запроса получения данных монитора.
    """
    channel: str = Field(..., description="Имя канала монитора для получения данных")

class GetAdapterDataRequest(AstraAddrRequest):
    """
    Модель для запроса получения данных адаптера.
    """
    name_adapter: str = Field(..., description="Имя адаптера для получения данных")

class GetPsiChannelRequest(AstraAddrRequest):
    """
    Модель для запроса получения PSI канала.
    """
    channel: str = Field(..., description="Имя PSI канала для получения данных")

class UpdateMonitorChannelRequest(AstraAddrRequest):
    """
    Модель для запроса обновления параметров монитора канала.
    """
    channel: str = Field(..., description="Имя канала монитора для обновления")
    analyze: Optional[str] = Field(None, description="Параметр 'analyze'")
    time_check: Optional[int] = Field(None, gt=0, description="Параметр 'time_check'")
    rate: Optional[int] = Field(None, gt=0, description="Параметр 'rate'")
    method_comparison: Optional[str] = Field(None, description="Параметр 'method_comparison'")

class UpdateMonitorDvbRequest(AstraAddrRequest):
    """
    Модель для запроса обновления параметров DVB-монитора.
    """
    name_adapter: str = Field(..., description="Имя адаптера DVB-монитора для обновления")
    time_check: Optional[int] = Field(None, gt=0, description="Параметр 'time_check'")
    rate: Optional[int] = Field(None, gt=0, description="Параметр 'rate'")

class ReloadRequest(AstraAddrRequest):
    """
    Модель для запроса перезагрузки Astra.
    """
    delay: Optional[int] = Field(30, gt=0, description="Задержка перед перезагрузкой в секундах")

class ExitRequest(AstraAddrRequest):
    """
    Модель для запроса завершения работы Astra.
    """
    delay: Optional[int] = Field(30, gt=0, description="Задержка перед завершением работы в секундах")

class ErrorResponse(BaseModel):
    """
    Стандартизированная модель для ответов об ошибках API.
    """
    error: str = Field(..., description="Краткое описание ошибки")
    message: Optional[str] = Field(None, description="Подробное сообщение об ошибке")
    details: Optional[Any] = Field(None, description="Дополнительные детали ошибки (например, ошибки валидации)")

# Добавьте другие модели по мере необходимости для других эндпоинтов
