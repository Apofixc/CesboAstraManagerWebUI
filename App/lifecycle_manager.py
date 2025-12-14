"""
Модуль для управления жизненным циклом приложения Astra Web-UI.

Отвечает за инициализацию и завершение работы менеджеров, роутеров,
HTTP-клиентов и фоновых задач, обеспечивая корректный запуск и остановку приложения.
"""
import asyncio
import time
import logging
from typing import Any, Optional, Set

import httpx
from quart import Quart

from .config_manager import ConfigManager
from .instance_manager import InstanceManager
from .api_router import ApiRouter
from .proxy_router import ProxyRouter
from .error_handler import ErrorHandler

logger = logging.getLogger(__name__)

class LifecycleManager:
    """
    Управляет жизненным циклом приложения Quart, включая инициализацию
    и завершение работы менеджеров, роутеров и HTTP-клиентов.
    """

    def __init__(self, app: Quart, config_manager: ConfigManager, sse_tasks: Set[asyncio.Task]):
        """
        Инициализирует LifecycleManager.

        Args:
            app (Quart): Экземпляр приложения Quart.
            config_manager (ConfigManager): Экземпляр ConfigManager для доступа к конфигурации.
            sse_tasks (Set[asyncio.Task]): Набор для отслеживания активных SSE задач.
        """
        self.app = app
        self.config_manager = config_manager
        self._sse_tasks = sse_tasks
        self.instance_manager: Optional[InstanceManager] = None
        self.proxy_router_instance: Optional[ProxyRouter] = None
        self.api_router_instance: Optional[ApiRouter] = None
        self.error_handler: Optional[ErrorHandler] = None
        self.http_client_instance_manager: Optional[httpx.AsyncClient] = None
        self.http_client_proxy: Optional[httpx.AsyncClient] = None
        self._update_task: Optional[asyncio.Task] = None

    def set_app_and_sse_tasks(self, app: Quart, sse_tasks: Set[asyncio.Task]):
        """
        Устанавливает экземпляр приложения Quart и набор SSE задач.

        Этот метод используется для внедрения зависимостей, которые становятся
        доступными только после инициализации AppCore.

        Args:
            app (Quart): Экземпляр приложения Quart.
            sse_tasks (Set[asyncio.Task]): Набор для отслеживания активных SSE задач.
        """
        self.app = app
        self._sse_tasks = sse_tasks
        logger.debug("Экземпляр Quart приложения и SSE задачи установлены в LifecycleManager.")

    def _create_http_client(self, timeout: float) -> httpx.AsyncClient:
        """Создает и возвращает асинхронный HTTP-клиент с заданным таймаутом."""
        return httpx.AsyncClient(timeout=timeout)

    async def _initialize_http_clients(self, config: Any):
        """Инициализирует асинхронные HTTP-клиенты."""
        self.http_client_instance_manager = self._create_http_client(config.scan_timeout)
        self.http_client_proxy = self._create_http_client(config.proxy_timeout)
        logger.debug("HTTP-клиенты инициализированы.")

    async def _initialize_managers_and_routers(self, app_core_instance: Any):
        """Инициализирует менеджеры и роутеры."""
        if not self.http_client_instance_manager or not self.http_client_proxy:
            raise RuntimeError("HTTP-клиенты не инициализированы перед менеджерами/роутерами.")

        self.instance_manager = InstanceManager(self.config_manager, self.http_client_instance_manager)
        self.proxy_router_instance = ProxyRouter(self.config_manager,
                                                self.instance_manager,
                                                self.http_client_proxy)
        self.api_router_instance = ApiRouter(self.instance_manager, app_core_instance)
        logger.debug("Менеджеры и роутеры инициализированы.")

    def _register_blueprints(self):
        """Регистрирует Blueprints в приложении."""
        if not self.api_router_instance or not self.proxy_router_instance:
            raise RuntimeError("Роутеры не инициализированы перед регистрацией Blueprints.")

        self.app.register_blueprint(self.api_router_instance.get_blueprint())
        self.app.register_blueprint(self.proxy_router_instance.get_blueprint())
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

    def _log_router_presence_on_shutdown(self):
        """Логирует наличие экземпляров роутеров и обработчика ошибок при завершении работы."""
        if self.api_router_instance:
            logger.debug("ApiRouter instance is present during shutdown.")
        if self.proxy_router_instance:
            logger.debug("ProxyRouter instance is present during shutdown.")
        if self.error_handler:
            logger.debug("ErrorHandler instance is present during shutdown.")

    async def startup(self, app_core_instance: Any):
        """
        Выполняет операции запуска приложения.

        Инициализирует конфигурацию, HTTP-клиенты, менеджеры и роутеры,
        регистрирует Blueprints и запускает фоновый цикл обновлений.

        Args:
            app_core_instance (Any): Экземпляр AppCore для доступа к его свойствам.
        """
        await self.config_manager.async_init()
        config = self.config_manager.get_config()

        await self._initialize_http_clients(config)
        await self._initialize_managers_and_routers(app_core_instance)
        self._register_blueprints()
        await self._start_update_loop()
        logger.info("Сервер запускается. Запуск фонового цикла обновлений.")

    async def shutdown(self):
        """
        Выполняет операции завершения работы приложения.

        Отменяет фоновые задачи, сохраняет кэш конфигурации и закрывает HTTP-клиенты.
        """
        logger.info("Сервер останавливается: начало процесса завершения работы.")

        await self._cancel_update_task()
        await self._cancel_sse_tasks()
        await self._cancel_pending_save_task()
        await self._update_and_save_config_cache()
        await self._close_http_clients()

        self._log_router_presence_on_shutdown()
        self._log_remaining_tasks()
        logger.info("Сервер остановлен: процесс завершения работы завершен.")
