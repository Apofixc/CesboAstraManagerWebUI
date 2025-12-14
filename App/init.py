"""
Модуль инициализации ядра приложения Astra Web-UI.

Отвечает за создание и конфигурирование экземпляра Quart-приложения,
управление зависимостями, регистрацию маршрутов и обработчиков ошибок,
а также настройку событий жизненного цикла приложения.
"""
import asyncio
import logging
from typing import Optional

from quart import Quart  # type: ignore
from quart_cors import cors  # type: ignore

from .api_router import ApiRouter
from .config_manager import ConfigManager
from .error_handler import ErrorHandler
from .instance_manager import InstanceManager
from .proxy_router import ProxyRouter
from .lifecycle_manager import LifecycleManager # Импорт нового класса

logger = logging.getLogger(__name__)


class AppCore:
    """
    Класс ядра приложения.

    Отвечает за инициализацию, конфигурирование, управление зависимостями (DI)
    и настройку жизненного цикла приложения Quart.
    """

    def __init__(self, config_manager: ConfigManager, lifecycle_manager: LifecycleManager):
        """
        Инициализирует основные компоненты приложения и сервер Quart.

        Args:
            config_manager (ConfigManager): Экземпляр ConfigManager.
            lifecycle_manager (LifecycleManager): Экземпляр LifecycleManager.
        """
        self.config_manager: ConfigManager = config_manager
        self.lifecycle_manager: LifecycleManager = lifecycle_manager
        self.app: Quart = Quart("Astra Web-UI")
        self._sse_tasks: set[asyncio.Task] = set() # Для отслеживания активных SSE задач
        self.lifecycle_manager.set_app_and_sse_tasks(self.app, self._sse_tasks)

    def add_sse_task(self, task: asyncio.Task):
        """Добавляет SSE задачу в отслеживаемый набор."""
        self._sse_tasks.add(task)
        logger.debug("SSE задача добавлена: %s", task.get_name())

    def remove_sse_task(self, task: asyncio.Task):
        """Удаляет SSE задачу из отслеживаемого набора."""
        if task in self._sse_tasks:
            self._sse_tasks.remove(task)
            logger.debug("SSE задача удалена: %s", task.get_name())
        else:
            logger.debug("Попытка удалить SSE задачу, которая не отслеживается: %s", task.get_name())

    @property
    def instance_manager(self) -> Optional[InstanceManager]:
        """
        Возвращает экземпляр InstanceManager из LifecycleManager.

        Returns:
            Optional[InstanceManager]: Экземпляр InstanceManager или None.
        """
        return self.lifecycle_manager.instance_manager

    @property
    def proxy_router_instance(self) -> Optional[ProxyRouter]:
        """
        Возвращает экземпляр ProxyRouter из LifecycleManager.

        Returns:
            Optional[ProxyRouter]: Экземпляр ProxyRouter или None.
        """
        return self.lifecycle_manager.proxy_router_instance

    @property
    def api_router_instance(self) -> Optional[ApiRouter]:
        """
        Возвращает экземпляр ApiRouter из LifecycleManager.

        Returns:
            Optional[ApiRouter]: Экземпляр ApiRouter или None.
        """
        return self.lifecycle_manager.api_router_instance

    @property
    def error_handler(self) -> Optional[ErrorHandler]:
        """
        Возвращает экземпляр ErrorHandler из LifecycleManager.

        Returns:
            Optional[ErrorHandler]: Экземпляр ErrorHandler или None.
        """
        return self.lifecycle_manager.error_handler

    def create_app(self) -> Quart:
        """
        Создает, конфигурирует и возвращает готовый к запуску экземпляр Quart-приложения.

        Метод выполняет внедрение зависимостей между менеджерами и роутерами,
        регистрирует Blueprints, обработчики ошибок и события жизненного цикла.

        Returns:
            Quart: Полностью сконфигурированный экземпляр Quart-приложения.
        """
        app = self.app

        self._setup_app_middleware_and_error_handling(app)
        self._register_lifecycle_events(app)

        logger.info("Сервер инициализирован.")
        return app

    def _setup_app_middleware_and_error_handling(self, app: Quart):
        """Настраивает middleware и обработку ошибок для приложения."""
        app = cors(app, allow_origin="*")
        self.lifecycle_manager.error_handler = ErrorHandler(app)
        logger.debug("Middleware и обработка ошибок настроены.")

    def _register_lifecycle_events(self, app: Quart):
        """Регистрирует обработчики событий жизненного цикла приложения."""

        @app.before_serving
        async def startup_event():
            """Обработчик события перед запуском сервера."""
            await self.lifecycle_manager.startup(self)
            logger.info("Сервер запускается. Запуск фонового цикла обновлений.")

        @app.after_serving
        async def shutdown_event():
            """Обработчик события после остановки сервера."""
            await self.lifecycle_manager.shutdown()
            logger.info("Сервер остановлен: процесс завершения работы завершен.")
            await self.lifecycle_manager.shutdown()
            logger.info("Сервер остановлен: процесс завершения работы завершен.")
