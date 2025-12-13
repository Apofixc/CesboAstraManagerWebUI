"""
Модуль для управления и мониторинга состояния экземпляров (инстансов) Astra.

Отвечает за периодическое сканирование сети, проверку доступности инстансов,
хранение их статуса и уведомление других частей приложения об изменениях.
"""
import asyncio
import logging
import time
from asyncio import Event as AsyncEvent
from asyncio import Lock
from typing import Any, Dict, List, Optional, Tuple

import httpx  # type: ignore

from .config_manager import ConfigManager

logger = logging.getLogger(__name__)


class InstanceManager:
    """
    Класс для управления и мониторинга состояния экземпляров (инстансов) Astra.

    Отвечает за периодическое сканирование сети, проверку доступности инстансов,
    хранение их статуса и уведомление других частей приложения об изменениях.
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, config_manager: ConfigManager, http_client: httpx.AsyncClient):
        """
        Инициализирует менеджер инстансов.

        Args:
            config_manager: Экземпляр ConfigManager для доступа к настройкам сканирования.
            http_client: Асинхронный HTTP-клиент для выполнения запросов.
        """
        self.config_manager = config_manager
        self.http_client = http_client
        self.instances: List[Dict[str, Any]] = []
        # Асинхронная блокировка для безопасного доступа к self.instances
        self.instances_lock: Lock = Lock()
        # Событие для оповещения подписчиков (например, SSE-клиентов) об обновлениях
        self.update_event: AsyncEvent = AsyncEvent()
        self._last_save_time: float = 0.0
        self._save_task: Optional[asyncio.Task] = None
        # Кэш для результатов check_instance_alive: {(host, port): (result, timestamp)}
        self._instance_alive_cache: Dict[Tuple[str, int], Tuple[Optional[Dict[str, Any]], float]] = {} # pylint: disable=C0301

        # Загрузка кэша из конфигурации при инициализации
        asyncio.create_task(self._load_initial_cache())

    async def _load_initial_cache(self) -> None:
        """
        Загружает кэш инстансов из конфигурации при старте приложения.
        """
        config = self.config_manager.get_config()
        instances = config.cached_instances
        timestamp = config.cache_timestamp
        cache_ttl = config.cache_ttl

        if instances and timestamp and (time.time() - timestamp < cache_ttl):
            async with self.instances_lock:
                self.instances[:] = instances
            logger.info("Инстансы загружены из конфигурационного кэша (%s шт.).", len(instances))
            self.update_event.set()
            self.update_event.clear()
        else:
            logger.info("Кэш инстансов в конфигурации устарел или недействителен.")

    async def check_instance_alive(self, host: str, port: int,
                                   scan_timeout: int) -> Optional[Dict[str, Any]]:
        """
        Асинхронно проверяет доступность одного экземпляра Astra по API Health Check.

        Args:
            host: Хост инстанса.
            port: Порт инстанса.
            scan_timeout: Таймаут (в секундах) для HTTP-запроса.

        Returns:
            Словарь с данными о здоровье инстанса (JSON-ответ), если он онлайн, иначе None.
        """
        addr = f'{host}:{port}'
        cache_key = (host, port)
        config = self.config_manager.get_config()
        instance_alive_cache_ttl = config.instance_alive_cache_ttl

        # Проверяем кэш перед выполнением HTTP-запроса
        if cache_key in self._instance_alive_cache:
            cached_result, timestamp = self._instance_alive_cache[cache_key]
            if (time.time() - timestamp) < instance_alive_cache_ttl:
                logger.debug("Возвращаем кэшированный результат для %s", addr)
                return cached_result

        result = None
        try:
            res = await self.http_client.get(f'http://{host}:{port}/api/health',
                                             timeout=scan_timeout)
            if res.status_code == 200:
                try:
                    result = res.json()
                except ValueError:
                    logger.warning("Неверный JSON-ответ от %s", addr)
        except httpx.RequestError as err:
            logger.warning("Не удалось подключиться к %s: %s", addr, err)

        # Кэшируем результат
        self._instance_alive_cache[cache_key] = (result, time.time())
        return result

    async def perform_update(self):
        """
        Асинхронно обновляет список активных инстансов Astra с параллельным сканированием.

        Метод запускает проверку всех сконфигурированных или сканируемых адресов
        параллельно с помощью `asyncio.gather`, обновляет внутреннее состояние `self.instances`
        и устанавливает `self.update_event` при обнаружении изменений.
        """
        config = self.config_manager.get_config()
        async with self.instances_lock:
            old_instances = {inst['addr']: inst for inst in self.instances}
        temp_instances: Dict[str, Dict[str, Any]] = {}

        host = config.instance_host
        start_port = config.start_port
        end_port = config.end_port
        servers = config.servers
        scan_timeout = config.scan_timeout

        target_addresses: List[Any] = []
        if servers:
            # Цели из списка конфигурации
            target_addresses = [(srv['host'], srv['port'], 'list') for srv in servers]
        else:
            # Цели из диапазона сканирования
            target_addresses = [(host, p, 'autoscan') for p in range(start_port, end_port + 1)]

        # Создание асинхронных задач
        tasks = [self.check_instance_alive(srv_host, srv_port, scan_timeout)
                 for srv_host, srv_port, _ in target_addresses]

        # Параллельное выполнение всех задач
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Обработка результатов
        for (srv_host, srv_port, srv_type), result in zip(target_addresses, results):
            addr = f'{srv_host}:{srv_port}'

            if isinstance(result, Exception) or result is None:
                # Сервер недоступен (ошибка подключения или таймаут)
                logger.debug("Сервер %s: оффлайн или ошибка: %s", addr, result)
                # Если сервер был известен ранее или он из списка (не автоскан),
                # сохраняем его как Offline
                if addr in old_instances or srv_type != 'autoscan':
                    version = old_instances.get(addr, {}).get('version', 'unknown')
                    temp_instances[addr] = {
                        'version': version,
                        'status': 'Offline'
                    }
            else:
                # Успешный результат
                instance_data = result
                temp_instances[addr] = {
                    'version': instance_data.get('version', 'unknown'),  # type: ignore
                    'status': 'Online'
                }
                logger.info("Сервер %s: онлайн, версия %s", addr,
                            temp_instances[addr]['version']) # pylint: disable=C0301

        # Атомарное обновление instances
        async with self.instances_lock:
            self.instances[:] = [{'addr': addr, **data} for addr, data in temp_instances.items()]

        # Проверка на изменения для установки события update_event
        # Используем сравнение хэшей для более быстрой проверки изменений
        has_changed = False
        new_instances_list = [{'addr': addr, **data} for addr, data in temp_instances.items()]
        old_instances_list = list(old_instances.values())  # Преобразуем словарь в список для сравнения

        # Сортируем списки для обеспечения консистентного порядка перед сравнением
        new_instances_list.sort(key=lambda x: x['addr'])
        old_instances_list.sort(key=lambda x: x['addr'])

        if new_instances_list != old_instances_list:
            has_changed = True

        if has_changed:
            # Обновляем кэш в конфигурации и сохраняем его
            config = self.config_manager.get_config()
            config.cached_instances = self.instances.copy()
            config.cache_timestamp = time.time()

            # Используем механизм debounce для сохранения конфигурации
            await self._debounce_save_config()

            # Устанавливаем и сразу сбрасываем событие, чтобы разбудить ожидающие корутины
            self.update_event.set()
            self.update_event.clear()
        else:
            logger.debug("Изменений в инстансах не обнаружено, кэш не обновляется.")

        logger.info("Обновлено %s инстансов", len(self.instances))

    async def async_update_loop(self):
        """
        Асинхронный цикл бесконечного обновления инстансов с интервалом.

        Запускается как фоновая задача Quart и работает на протяжении всего
        времени жизни приложения, периодически вызывая `perform_update`.
        """
        config = self.config_manager.get_config()
        check_interval = config.check_interval

        # Запускаем первое обновление сразу при старте цикла
        await self.perform_update()

        while True:
            try:
                # Ожидание интервала перед следующим обновлением
                await asyncio.sleep(check_interval)
                await self.perform_update()
            except Exception as err:  # pylint: disable=W0718 # Catching too general exception to keep loop running
                # Логирование ошибок цикла, чтобы он не прерывался полностью
                logger.error("Ошибка в цикле обновлений: %s", err)
                await asyncio.sleep(check_interval)

    async def check_instance_online(self, addr: str) -> bool:
        """
        Проверяет, помечен ли конкретный инстанс как 'Online' в текущем списке.

        Args:
            addr: Адрес инстанса в формате "хост:порт".

        Returns:
            True, если инстанс онлайн, False в противном случае.
        """
        async with self.instances_lock:
            return any(i['addr'] == addr and i['status'] == 'Online' for i in self.instances)

    async def get_instances(self) -> List[Dict[str, Any]]:
        """
        Возвращает текущий список инстансов.

        Returns:
            Копия списка всех отслеживаемых инстансов с их статусами и версиями.
        """
        async with self.instances_lock:
            return self.instances.copy()

    async def _debounce_save_config(self, delay: float = 5.0) -> None:
        """
        Откладывает сохранение конфигурации, чтобы избежать слишком частых записей на диск.
        Если новая задача сохранения приходит до завершения задержки, предыдущая отменяется.
        """
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            try:
                await self._save_task  # Ожидаем отмены, чтобы избежать RuntimeError
            except asyncio.CancelledError:
                pass  # Ожидаемое исключение при отмене

        async def _save_task_coro():
            try:
                await asyncio.sleep(delay)
                await self.config_manager.save_config()
                logger.info("Конфигурация успешно сохранена после задержки.")
            except asyncio.CancelledError:
                logger.debug("Задача сохранения конфигурации отменена.")
            except Exception as err:  # pylint: disable=W0718 # Catching too general exception for logging
                logger.error("Ошибка при отложенном сохранении конфигурации: %s", err)

        self._save_task = asyncio.create_task(_save_task_coro())

    async def manual_update(self) -> List[Dict[str, Any]]:
        """
        Запускает немедленное обновление списка инстансов (используется API-эндпоинтом).

        Returns:
            Обновленный список инстансов после завершения сканирования.
        """
        await self.perform_update()
        return await self.get_instances()
