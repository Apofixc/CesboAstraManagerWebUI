"""
Модуль инициализации ядра приложения Astra Web-UI.

Отвечает за создание и конфигурирование экземпляра Quart-приложения,
управление зависимостями, регистрацию маршрутов и обработчиков ошибок,
а также настройку событий жизненного цикла приложения.
"""
import asyncio
import logging
from typing import Optional

import httpx  # type: ignore
from quart import Quart  # type: ignore
from quart_cors import cors  # type: ignore

from App.api_router import ApiRouter
from App.config_manager import ConfigManager, AppConfig # Добавляем импорт AppConfig
from App.error_handler import ErrorHandler
from App.instance_manager import InstanceManager
from App.proxy_router import ProxyRouter

logger = logging.getLogger(__name__)


class AppCore:
    """
    Класс ядра приложения.

    Отвечает за инициализацию, конфигурирование, управление зависимостями (DI)
    и настройку жизненного цикла приложения Quart.
    """

    def __init__(self, config_manager: ConfigManager):
        """
        Инициализирует основные компоненты приложения и сервер Quart.

        Args:
            config_manager (ConfigManager): Экземпляр ConfigManager с загруженной конфигурацией.
        """
        self.config_manager: ConfigManager = config_manager
        # Эти менеджеры будут инициализированы позже в create_app
        self.instance_manager: Optional[InstanceManager] = None
        self.proxy_router_instance: Optional[ProxyRouter] = None
        self.api_router_instance: Optional[ApiRouter] = None
        self.app: Quart = Quart("Astra Web-UI")
        self.error_handler: Optional[ErrorHandler] = None
        self.http_client: Optional[httpx.AsyncClient] = None

    def create_app(self) -> Quart:
        """
        Создает, конфигурирует и возвращает готовый к запуску экземпляр Quart-приложения.

        Метод выполняет внедрение зависимостей между менеджерами и роутерами,
        регистрирует Blueprints, обработчики ошибок и события жизненного цикла.

        Returns:
            Quart: Полностью сконфигурированный экземпляр Quart-приложения.
        """
        app = self.app

        # Конфигурация уже загружена и доступна через self.config_manager.get_config()
        config = self.config_manager.get_config()

        # Инициализация httpx.AsyncClient с таймаутом из конфигурации
        self.http_client = httpx.AsyncClient(timeout=config.scan_timeout)

        # Инициализация компонентов, которые зависят от менеджеров
        self.instance_manager = InstanceManager(self.config_manager, self.http_client)
        self.proxy_router_instance = ProxyRouter(self.config_manager,
                                                 self.instance_manager,
                                                 self.http_client)
        self.api_router_instance = ApiRouter(self.instance_manager)

        # Middleware: Включение CORS для всех источников
        app = cors(app, allow_origin="*")

        # Регистрация роутеров (Blueprints)
        if self.api_router_instance and self.proxy_router_instance:
            app.register_blueprint(self.api_router_instance.get_blueprint())
            app.register_blueprint(self.proxy_router_instance.get_blueprint())

        # Регистрация обработчиков ошибок
        self.error_handler = ErrorHandler(app)

        # События жизненного цикла приложения
        @app.before_serving
        async def startup_event():
            """
            Обработчик события перед запуском сервера.

            Запускает фоновую задачу обновления инстансов.
            """
            logger.info("Сервер запускается. Запуск фонового цикла обновлений.")
            if self.instance_manager:
                # Запускаем цикл обновлений как фоновую задачу asyncio
                asyncio.create_task(self.instance_manager.async_update_loop())

        @app.after_serving
        async def shutdown_event():
            """
            Обработчик события после остановки сервера.

            Закрывает HTTP-клиент.
            """
            logger.info("Сервер останавливается.")
            if self.http_client:
                await self.http_client.aclose()

        logger.info("Сервер инициализирован.")
        return app
