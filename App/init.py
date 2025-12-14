"""
Модуль инициализации ядра приложения Astra Web-UI.

Отвечает за создание и конфигурирование экземпляра Quart-приложения,
управление зависимостями, регистрацию маршрутов и обработчиков ошибок,
а также настройку событий жизненного цикла приложения.
"""
import asyncio
import time
from typing import Any, Optional
import logging

import httpx  # type: ignore
from quart import Quart  # type: ignore
from quart_cors import cors  # type: ignore

from astra_manager.App.api_router import ApiRouter
from astra_manager.App.config_manager import ConfigManager
from astra_manager.App.error_handler import ErrorHandler
from astra_manager.App.instance_manager import InstanceManager
from astra_manager.App.proxy_router import ProxyRouter

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
            config_path (Optional[str]): Путь к файлу конфигурации.
                                        Если `None`, используются дефолтные настройки.
        """
        self.config_manager: ConfigManager = ConfigManager(config_path)
        self.app: Quart = Quart("Astra Web-UI")
        self.instance_manager: Optional[InstanceManager] = None
        self.proxy_router_instance: Optional[ProxyRouter] = None
        self.api_router_instance: Optional[ApiRouter] = None
        self.error_handler: Optional[ErrorHandler] = None
        self.http_client_instance_manager: Optional[httpx.AsyncClient] = None
        self.http_client_proxy: Optional[httpx.AsyncClient] = None
        self._update_task: Optional[asyncio.Task] = None
        self._sse_tasks: set[asyncio.Task] = set() # Для отслеживания активных SSE задач

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

    async def _initialize_http_clients(self, config: Any):
        """Инициализирует асинхронные HTTP-клиенты."""
        self.http_client_instance_manager = httpx.AsyncClient(timeout=config.scan_timeout)
        self.http_client_proxy = httpx.AsyncClient(timeout=config.proxy_timeout)
        logger.debug("HTTP-клиенты инициализированы.")

    async def _initialize_managers_and_routers(self):
        """Инициализирует менеджеры и роутеры."""
        if not self.http_client_instance_manager or not self.http_client_proxy:
            raise RuntimeError("HTTP-клиенты не инициализированы перед менеджерами/роутерами.")

        self.instance_manager = InstanceManager(self.config_manager, self.http_client_instance_manager)
        self.proxy_router_instance = ProxyRouter(self.config_manager,
                                                self.instance_manager,
                                                self.http_client_proxy)
        self.api_router_instance = ApiRouter(self.instance_manager, self)
        logger.debug("Менеджеры и роутеры инициализированы.")

    def _register_blueprints(self, app: Quart):
        """Регистрирует Blueprints в приложении."""
        if not self.api_router_instance or not self.proxy_router_instance:
            raise RuntimeError("Роутеры не инициализированы перед регистрацией Blueprints.")

        app.register_blueprint(self.api_router_instance.get_blueprint())
        app.register_blueprint(self.proxy_router_instance.get_blueprint())
        logger.debug("Blueprints зарегистрированы.")

    async def _start_update_loop(self):
        """Загружает начальный кэш и запускает фоновый цикл обновлений."""
        if self.instance_manager:
            await self.instance_manager.load_initial_cache()
            self._update_task = asyncio.create_task(self.instance_manager.async_update_loop())
            logger.info("Фоновый цикл обновлений запущен.")
        else:
            logger.warning("InstanceManager не инициализирован, фоновый цикл обновлений не запущен.")

    async def _cancel_update_task(self):
        """Отменяет фоновую задачу обновления инстансов."""
        logger.info("Попытка отмены фоновой задачи обновления инстансов.")
        if self._update_task:
            self._update_task.cancel()
            try:
                logger.info("Ожидание завершения фоновой задачи обновления инстансов (таймаут 10 секунд).")
                await asyncio.wait_for(self._update_task, timeout=10.0)
                logger.info("Фоновая задача обновления инстансов завершена корректно.")
            except asyncio.CancelledError:
                logger.info("Фоновая задача обновления инстансов отменена.")
            except asyncio.TimeoutError:
                logger.warning(
                    "Фоновая задача обновления инстансов не завершилась в течение 10 секунд "
                    "после отмены. Возможно, она все еще выполняется."
                )
            except (RuntimeError, asyncio.InvalidStateError) as e:
                logger.error(
                    "Ошибка при завершении фоновой задачи обновления инстансов: %s",
                    e, exc_info=True
                )
        else:
            logger.info("Фоновая задача обновления инстансов не активна.")

    async def _cancel_sse_tasks(self):
        """Отменяет все активные SSE задачи."""
        logger.info("Попытка отмены активных SSE задач.")
        if self._sse_tasks:
            logger.info("Найдено %d активных SSE задач. Начало отмены.", len(self._sse_tasks))
            tasks_to_wait = list(self._sse_tasks)
            for task in tasks_to_wait:
                task.cancel()
            done, pending = await asyncio.wait(tasks_to_wait, timeout=5.0, return_when=asyncio.ALL_COMPLETED)
            for task in done:
                if task.exception():
                    logger.error("Ошибка в завершенной SSE задаче: %s", task.exception(), exc_info=True)
            for task in pending:
                logger.warning("SSE задача %s не завершилась в течение 5 секунд после отмены.", task.get_name())
            logger.info("Все активные SSE задачи отменены и завершены (или истек таймаут ожидания).")
            self._sse_tasks.clear()
        else:
            logger.info("Активных SSE задач не найдено.")

    async def _cancel_pending_save_task(self):
        """Отменяет отложенную задачу сохранения конфигурации."""
        logger.info("Начало отмены отложенной задачи сохранения конфигурации.")
        if self.instance_manager:
            await self.instance_manager.cancel_pending_save_task()
            logger.info("Отложенная задача сохранения конфигурации отменена (если была активна).")
        else:
            logger.info("InstanceManager не инициализирован, отложенная задача сохранения конфигурации не отменялась.")

    async def _update_and_save_config_cache(self):
        """Обновляет кэш инстансов в конфигурации и сохраняет его."""
        if self.instance_manager:
            config = self.config_manager.get_config()
            async with self.instance_manager.instances_lock:
                config.cached_instances = self.instance_manager.instances.copy()
            config.cache_timestamp = time.time()
            logger.info("Кэш инстансов обновлен в конфигурации.")
        else:
            logger.info("InstanceManager не инициализирован, кэш инстансов не обновлялся.")

        logger.info("Начало сохранения конфигурации.")
        await self.config_manager.save_config()
        logger.info("Конфигурация успешно сохранена.")

    async def _close_http_clients(self):
        """Закрывает HTTP-клиенты."""
        logger.info("Начало закрытия HTTP-клиентов.")
        if self.http_client_instance_manager:
            await self.http_client_instance_manager.aclose()
            logger.info("HTTP-клиент для InstanceManager закрыт.")
        else:
            logger.info("HTTP-клиент для InstanceManager не инициализирован.")

        if self.http_client_proxy:
            await self.http_client_proxy.aclose()
            logger.info("HTTP-клиент для ProxyRouter закрыт.")
        else:
            logger.info("HTTP-клиент для ProxyRouter не инициализирован.")

    def _log_remaining_tasks(self):
        """Логирует все оставшиеся активные задачи."""
        remaining_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if remaining_tasks:
            logger.warning("При завершении работы остались активные задачи: %s", [t.get_name() for t in remaining_tasks])
        else:
            logger.info("При завершении работы активных задач не осталось.")

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
        self.error_handler = ErrorHandler(app)
        logger.debug("Middleware и обработка ошибок настроены.")

    def _register_lifecycle_events(self, app: Quart):
        """Регистрирует обработчики событий жизненного цикла приложения."""

        @app.before_serving
        async def startup_event():
            """Обработчик события перед запуском сервера."""
            await self.config_manager.async_init()
            config = self.config_manager.get_config()

            await self._initialize_http_clients(config)
            await self._initialize_managers_and_routers()
            self._register_blueprints(app)
            await self._start_update_loop()
            logger.info("Сервер запускается. Запуск фонового цикла обновлений.")

        @app.after_serving
        async def shutdown_event():
            """Обработчик события после остановки сервера."""
            logger.info("Сервер останавливается: начало процесса завершения работы.")

            await self._cancel_update_task()
            await self._cancel_sse_tasks()
            await self._cancel_pending_save_task()
            await self._update_and_save_config_cache()
            await self._close_http_clients()

            self._log_router_presence_on_shutdown()
            self._log_remaining_tasks()
            logger.info("Сервер остановлен: процесс завершения работы завершен.")

    def _log_router_presence_on_shutdown(self):
        """Логирует наличие экземпляров роутеров и обработчика ошибок при завершении работы."""
        if self.api_router_instance:
            logger.debug("ApiRouter instance is present during shutdown.")
        if self.proxy_router_instance:
            logger.debug("ProxyRouter instance is present during shutdown.")
        if self.error_handler:
            logger.debug("ErrorHandler instance is present during shutdown.")
