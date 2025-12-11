import logging
from quart import Quart # type: ignore
from quart_cors import cors # type: ignore
from starlette.staticfiles import StaticFiles # type: ignore
import asyncio
from typing import Optional
import httpx  # type: ignore

from App.config_manager import ConfigManager
from App.instance_manager import InstanceManager
from App.proxy_router import ProxyRouter
from App.api_router import ApiRouter
from App.error_handler import ErrorHandler

logger = logging.getLogger(__name__)

class AppCore:
    """
    Класс ядра приложения.

    Отвечает за инициализацию, конфигурирование, управление зависимостями (DI)
    и настройку жизненного цикла приложения Quart.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Инициализирует основные компоненты приложения и сервер Quart.

        Args:
            config_path: Путь к файлу конфигурации. Если None, используются дефолтные настройки.
        """
        self.config_manager: ConfigManager = ConfigManager(config_path)
        # Эти менеджеры будут инициализированы позже в create_app
        self.instance_manager: Optional[InstanceManager] = None
        self.proxy_router_instance: Optional[ProxyRouter] = None
        self.api_router_instance: Optional[ApiRouter] = None
        self.app: Quart = Quart("Astra Web-UI")
        self.error_handler: Optional[ErrorHandler] = None
        self.http_client: httpx.AsyncClient = None

    def create_app(self) -> Quart:
        """
        Создаёт, конфигурирует и возвращает готовый к запуску экземпляр Quart-приложения.

        Метод выполняет внедрение зависимостей между менеджерами и роутерами,
        регистрирует Blueprints, обработчики ошибок и события жизненного цикла.

        Returns:
            Полностью сконфигурированный экземпляр Quart-приложения.
        """
        app = self.app

        self.http_client: httpx.AsyncClient = httpx.AsyncClient() 

        # Инициализация компонентов, которые зависят от менеджеров
        self.instance_manager = InstanceManager(self.config_manager, self.http_client)
        self.proxy_router_instance = ProxyRouter(self.config_manager, self.instance_manager, self.http_client)
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
            """Обработчик события перед запуском сервера: запускает фоновую задачу обновления инстансов."""
            logger.info("Сервер запускается. Запуск фонового цикла обновлений.")
            if self.instance_manager:
                 # Запускаем цикл обновлений как фоновую задачу asyncio
                asyncio.create_task(self.instance_manager.async_update_loop()) 

        @app.after_serving
        async def shutdown_event():
            """Обработчик события после остановки сервера."""
            logger.info("Сервер останавливается.")
            await self.http_client.aclose()

        logger.info("Сервер инициализирован.")
        return app
