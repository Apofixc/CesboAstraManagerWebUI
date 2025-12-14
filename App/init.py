"""
Модуль инициализации ядра приложения Astra Web-UI.

Отвечает за создание и конфигурирование экземпляра Quart-приложения,
управление зависимостями, регистрацию маршрутов и обработчиков ошибок,
а также настройку событий жизненного цикла приложения.
"""
import asyncio
import time
from typing import Optional
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

    def create_app(self) -> Quart:
        """
        Создает, конфигурирует и возвращает готовый к запуску экземпляр Quart-приложения.

        Метод выполняет внедрение зависимостей между менеджерами и роутерами,
        регистрирует Blueprints, обработчики ошибок и события жизненного цикла.

        Returns:
            Quart: Полностью сконфигурированный экземпляр Quart-приложения.
        """
        app = self.app

        # Middleware: Включение CORS для всех источников
        app = cors(app, allow_origin="*")

        # Регистрация обработчиков ошибок
        self.error_handler = ErrorHandler(app)

        # События жизненного цикла приложения
        @app.before_serving
        async def startup_event():
            """
            Обработчик события перед запуском сервера.

            Запускает фоновую задачу обновления инстансов.
            """
            await self.config_manager.async_init()
            config = self.config_manager.get_config()

            # Инициализация httpx.AsyncClient для InstanceManager с таймаутом сканирования
            self.http_client_instance_manager = httpx.AsyncClient(timeout=config.scan_timeout)
            # Инициализация httpx.AsyncClient для ProxyRouter с таймаутом из конфигурации
            self.http_client_proxy = httpx.AsyncClient(timeout=config.proxy_timeout)
            # Инициализация компонентов, которые зависят от менеджеров
            self.instance_manager = InstanceManager(self.config_manager, self.http_client_instance_manager)
            self.proxy_router_instance = ProxyRouter(self.config_manager,
                                                    self.instance_manager,
                                                    self.http_client_proxy)
            self.api_router_instance = ApiRouter(self.instance_manager)

            # Регистрация роутеров (Blueprints)
            app.register_blueprint(self.api_router_instance.get_blueprint())
            app.register_blueprint(self.proxy_router_instance.get_blueprint())
            logger.info("Сервер запускается. Запуск фонового цикла обновлений.")
            if self.instance_manager:
                # Синхронная загрузка кэша при старте приложения
                await self.instance_manager.load_initial_cache()
                # Запускаем цикл обновлений как фоновую задачу asyncio
                self._update_task = asyncio.create_task(self.instance_manager.async_update_loop())

        @app.after_serving
        async def shutdown_event():
            """
            Обработчик события после остановки сервера.

            Закрывает HTTP-клиенты и отменяет фоновую задачу.
            """
            logger.info("Сервер останавливается.")
            if self._update_task:
                self._update_task.cancel()
                try:
                    # Ожидаем завершения задачи с таймаутом
                    logger.info("Ожидание завершения фоновой задачи обновления инстансов (таймаут 10 секунд).")
                    await asyncio.wait_for(self._update_task, timeout=10.0)
                    logger.info("Фоновая задача обновления инстансов завершена корректно.")
                except asyncio.CancelledError:
                    logger.info("Фоновая задача обновления инстансов отменена.")
                except asyncio.TimeoutError:
                    logger.warning("Фоновая задача обновления инстансов не завершилась в течение 10 секунд после отмены. Возможно, она все еще выполняется.")
                except Exception as e:
                    logger.error("Ошибка при завершении фоновой задачи обновления инстансов: %s", e, exc_info=True)

            logger.info("Начало отмены отложенной задачи сохранения конфигурации.")
            if self.instance_manager:
                await self.instance_manager.cancel_pending_save_task()
                logger.info("Отложенная задача сохранения конфигурации отменена (если была активна).")
            # Обновляем кэш в конфигурации из instance_manager перед сохранением
            if self.instance_manager:
                config = self.config_manager.get_config()
                async with self.instance_manager.instances_lock:
                    config.cached_instances = self.instance_manager.instances.copy()
                config.cache_timestamp = time.time() # Обновляем временную метку
            logger.info("Начало сохранения конфигурации.")
            await self.config_manager.save_config()
            logger.info("Конфигурация успешно сохранена.")

            logger.info("Начало закрытия HTTP-клиентов.")
            if self.http_client_instance_manager:
                await self.http_client_instance_manager.aclose()
                logger.info("HTTP-клиент для InstanceManager закрыт.")
            if self.http_client_proxy:
                await self.http_client_proxy.aclose()
                logger.info("HTTP-клиент для ProxyRouter закрыт.")

            # Добавляем явные проверки на None для других менеджеров (для типобезопасности)
            if self.api_router_instance:
                logger.debug("ApiRouter instance is present during shutdown.")
            if self.proxy_router_instance:
                logger.debug("ProxyRouter instance is present during shutdown.")
            if self.error_handler:
                logger.debug("ErrorHandler instance is present during shutdown.")
            logger.info("Сервер остановлен.")

        logger.info("Сервер инициализирован.")
        return app
