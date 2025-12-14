"""
Модуль для управления и мониторинга состояния экземпляров (инстансов) Astra.

Отвечает за периодическое сканирование сети, проверку доступности инстансов,
хранение их статуса и уведомление других частей приложения об изменениях.
"""
import asyncio
import time
from asyncio import Event as AsyncEvent
from asyncio import Lock
from typing import Any, Dict, List, Optional, Tuple
import logging
# ExceptionGroup является встроенным в Python 3.11+, поэтому явный импорт не требуется.
# from exceptiongroup import ExceptionGroup # type: ignore

import httpx  # type: ignore

from astra_manager.App.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class InstanceManager:
    """
    Класс для управления и мониторинга состояния экземпляров (инстансов) Astra.

    Отвечает за периодическое сканирование сети, проверку доступности инстансов,
    хранение их статуса и уведомление других частей приложения об изменениях.
    """

    # pylint: disable=too-many-instance-attributes
    def __init__(self, config_manager: ConfigManager,
                 http_client: httpx.AsyncClient):
        """
        Инициализирует менеджер инстансов.

        Args:
            config_manager (ConfigManager): Экземпляр ConfigManager для доступа к 
                                            настройкам сканирования.
            http_client (httpx.AsyncClient): Асинхронный HTTP-клиент для выполнения запросов.
        """
        self.config_manager = config_manager
        self.http_client = http_client
        self.instances: List[Dict[str, Any]] = []
        # Асинхронная блокировка для безопасного доступа к self.instances
        self.instances_lock: Lock = Lock()
        # Событие для оповещения подписчиков (например, SSE-клиентов) об обновлениях
        self.update_event: AsyncEvent = AsyncEvent()
        self._save_task: Optional[asyncio.Task] = None
        # Кэш для результатов check_instance_alive: {(host, port): (result, timestamp)}
        self._instance_alive_cache: Dict[Tuple[str, int],
                                         Tuple[Optional[Dict[str, Any]], float]] = {}
        # Блокировка для защиты _instance_alive_cache
        self._instance_alive_cache_lock: Lock = Lock()

        # Загрузка кэша из конфигурации при инициализации (теперь синхронно из AppCore)
        # Закомментировано, так как загрузка теперь происходит в AppCore.startup_event

    async def load_initial_cache(self) -> None:
        """
        Загружает кэш инстансов из конфигурации при старте приложения.

        Если кэш существует и не устарел, он используется для инициализации
        списка инстансов. В противном случае кэш игнорируется.
        """
        config = self.config_manager.get_config()
        instances = config.cached_instances
        timestamp = config.cache_timestamp
        cache_ttl = config.cache_ttl

        if instances and timestamp and (time.time() - timestamp < cache_ttl):
            async with self.instances_lock:
                self.instances[:] = instances
            logger.info("Инстансы загружены из конфигурационного кэша (%s шт.).", len(instances))
        else:
            logger.info("Кэш инстансов в конфигурации устарел или недействителен.") # pylint: disable=C0301

    async def check_instance_alive(self, host: str, port: int,
                                   scan_timeout: int) -> Optional[Dict[str, Any]]:
        """
        Асинхронно проверяет доступность одного экземпляра Astra по API Health Check.

        Использует кэш для предотвращения избыточных HTTP-запросов.

        Args:
            host (str): Хост инстанса.
            port (int): Порт инстанса.
            scan_timeout (int): Таймаут (в секундах) для HTTP-запроса.

        Returns:
            Optional[Dict[str, Any]]: Словарь с данными о здоровье инстанса (JSON-ответ),
                                      если он онлайн, иначе `None`.
        """
        addr = f'{host}:{port}'
        cache_key = (host, port)
        config = self.config_manager.get_config()
        instance_alive_cache_ttl = config.instance_alive_cache_ttl

        # Проверяем кэш перед выполнением HTTP-запроса
        async with self._instance_alive_cache_lock:
            if cache_key in self._instance_alive_cache:
                cached_result, timestamp = self._instance_alive_cache[cache_key]
                if (time.time() - timestamp) < instance_alive_cache_ttl:
                    logger.debug("Возвращаем кэшированный результат для %s", addr)
                    return cached_result

        # Выполняем HTTP-запрос вне блокировки для максимального параллелизма
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

        # Кэшируем результат внутри блокировки
        async with self._instance_alive_cache_lock:
            self._instance_alive_cache[cache_key] = (result, time.time())
        return result

    def _get_updated_instance_data(self, addr: str, srv_type: str, result: Any,
                                    old_instances: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Определяет обновленные данные для одного инстанса на основе результата проверки.

        Args:
            addr (str): Адрес инстанса в формате "хост:порт".
            srv_type (str): Тип сервера ('list' для сконфигурированных, 
                            'autoscan' для автосканирования).
            result (Any): Результат проверки доступности инстанса (JSON-ответ или исключение).
            old_instances (Dict[str, Dict[str, Any]]): Словарь предыдущих состояний инстансов.

        Returns:
            Dict[str, Any]: Словарь с обновленными данными инстанса (версия, статус).
        """
        if isinstance(result, Exception) or result is None:
            logger.debug("Сервер %s: оффлайн или ошибка: %s", addr, result)
            # Логика: для сконфигурированных серверов (srv_type != 'autoscan')
            # или серверов, которые уже были в списке (old_instances),
            # сохраняем их в списке со статусом 'Offline'.
            # Для новых автосканированных серверов, которые стали оффлайн,
            # не добавляем их в список.
            if addr in old_instances or srv_type != 'autoscan':
                version = old_instances.get(addr, {}).get('version', 'unknown')
                return {
                    'version': version,
                    'status': 'Offline'
                }
            return {}
        instance_data = result
        logger.info("Сервер %s: онлайн, версия %s", addr, instance_data.get('version', 'unknown'))

        return {
            'version': instance_data.get('version', 'unknown'),  # type: ignore
            'status': 'Online'
        }

    async def perform_update(self):
        """
        Асинхронно обновляет список активных инстансов Astra.

        Метод запускает параллельную проверку всех сконфигурированных или сканируемых
        адресов, обновляет внутреннее состояние `self.instances` и устанавливает
        `self.update_event` при обнаружении изменений.
        """
        config = self.config_manager.get_config()
        async with self.instances_lock:
            old_instances = {inst['addr']: inst for inst in self.instances}
        temp_instances: Dict[str, Dict[str, Any]] = {}

        target_addresses = self._get_target_addresses(config)

        # Используем TaskGroup для более чистого управления асинхронными задачами
        # Требуется Python 3.11+
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(self.check_instance_alive(srv_host, srv_port, config.scan_timeout))
                         for srv_host, srv_port, _ in target_addresses]
        except* ExceptionGroup as eg: # type: ignore # Перехватываем ExceptionGroup
            logger.error("Ошибка в TaskGroup при проверке инстансов: %s", eg, exc_info=True)
            # Если все задачи отменены, TaskGroup может поднять CancelledError
            # или TaskGroupError, содержащую CancelledError.
            # Мы перехватываем это на уровне async_update_loop.
            raise # Перевыбрасываем, чтобы async_update_loop мог обработать

        results = [task.result() for task in tasks]

        for (srv_host, srv_port, srv_type), result in zip(target_addresses, results):
            addr = f'{srv_host}:{srv_port}'
            instance_data = self._get_updated_instance_data(addr, srv_type, result, old_instances)
            if instance_data:
                temp_instances[addr] = instance_data

        # Атомарное обновление instances
        async with self.instances_lock:
            self.instances[:] = [{'addr': addr, **data} for addr, data in temp_instances.items()]

        await self._check_for_changes_and_notify(old_instances, temp_instances, config)

    async def _check_for_changes_and_notify(self, old_instances: Dict[str, Dict[str, Any]],
                                            temp_instances: Dict[str, Dict[str, Any]],
                                            config: Any) -> None:
        """
        Проверяет наличие изменений в списке инстансов и уведомляет подписчиков.

        Если обнаружены изменения, обновляет кэш в конфигурации и сохраняет его
        с использованием механизма debounce, а также устанавливает событие `update_event`.

        Args:
            old_instances (Dict[str, Dict[str, Any]]): Словарь предыдущих состояний инстансов.
            temp_instances (Dict[str, Dict[str, Any]]): Словарь текущих состояний инстансов.
            config (Any): Объект конфигурации приложения.
        """
        has_changed = False
        new_instances_list = [{'addr': addr, **data} for addr, data in temp_instances.items()]
        # Преобразуем словарь в список для сравнения
        old_instances_list = list(old_instances.values())

        # Сортируем списки для обеспечения консистентного порядка перед сравнением
        new_instances_list.sort(key=lambda x: x['addr'])
        old_instances_list.sort(key=lambda x: x['addr'])

        if new_instances_list != old_instances_list:
            has_changed = True

        if has_changed:
            logger.info("Обнаружены изменения в инстансах, кэш обновлен.")
            # Обновляем кэш в конфигурации и сохраняем его
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

    def _get_target_addresses(self, config) -> List[Tuple[str, int, str]]:
        """
        Формирует список целевых адресов для сканирования.

        Список формируется на основе конфигурации: либо из явно указанных серверов,
        либо путем автосканирования диапазона портов.

        Args:
            config (AppConfig): Объект конфигурации приложения.

        Returns:
            List[Tuple[str, int, str]]: Список кортежей (хост, порт, тип_сканирования).
        """
        target_addresses: List[Tuple[str, int, str]] = []
        if config.servers:
            target_addresses = [(srv.address, srv.port, 'list') for srv in config.servers]
        else:
            target_addresses = [(config.instance_host, p, 'autoscan')
                                for p in range(config.start_port, config.end_port + 1)]
        return target_addresses

    async def async_update_loop(self):
        """
        Асинхронный цикл бесконечного обновления инстансов.

        Запускается как фоновая задача и периодически вызывает `perform_update`
        с интервалом, определенным в конфигурации.
        """
        config = self.config_manager.get_config()
        check_interval = config.check_interval
        while True:
            try:
                await self.perform_update()
            except asyncio.CancelledError:
                logger.info("Цикл обновлений инстансов отменен.")
                break # Завершаем цикл при отмене
            except ExceptionGroup as eg: # Перехватываем ExceptionGroup, если она была перевыброшена
                logger.error("Ошибка в TaskGroup при проверке инстансов: %s", eg, exc_info=True)
            except Exception as err: # Перехватываем другие непредвиденные исключения
                logger.error("Непредвиденная ошибка в цикле обновлений: %s", err, exc_info=True)
            # Ожидание интервала перед следующим обновлением
            logger.debug("Цикл обновлений: ожидание интервала %s секунд.", check_interval)
            await asyncio.sleep(check_interval)
            logger.debug("Цикл обновлений: интервал завершен, выполнение обновления.")

    async def check_instance_online(self, addr: str) -> bool:
        """
        Проверяет, помечен ли конкретный инстанс как 'Online' в текущем списке.

        Args:
            addr (str): Адрес инстанса в формате "хост:порт".

        Returns:
            bool: `True`, если инстанс онлайн, `False` в противном случае.
        """
        async with self.instances_lock:
            return any(i['addr'] == addr and i['status'] == 'Online' for i in self.instances)

    async def get_instances(self) -> List[Dict[str, Any]]:
        """
        Возвращает текущий список инстансов.

        Returns:
            List[Dict[str, Any]]: Копия списка всех отслеживаемых инстансов
                                  с их статусами и версиями.
        """
        async with self.instances_lock:
            return self.instances.copy()

    async def _debounce_save_config(self) -> None:
        # Этот метод остается защищенным, так как он является внутренней деталью реализации debounce.
        # Внешний код не должен напрямую управлять _save_task.
        """
        Откладывает сохранение конфигурации для предотвращения слишком частых записей на диск.

        Если новая задача сохранения приходит до завершения задержки,
        предыдущая задача отменяется.

        Args:
            delay (float): Задержка в секундах перед сохранением. По умолчанию 5.0.
        """
        config = self.config_manager.get_config() # Получаем config здесь, чтобы он был доступен
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            try:
                logger.debug("Ожидание завершения предыдущей задачи сохранения конфигурации (таймаут %s секунд).", config.debounce_save_delay + 1)
                await asyncio.wait_for(self._save_task, timeout=config.debounce_save_delay + 1) # Даем немного больше времени
                logger.debug("Предыдущая задача сохранения конфигурации завершена после отмены.")
            except asyncio.CancelledError:
                logger.debug("Предыдущая задача сохранения конфигурации отменена.")
            except asyncio.TimeoutError:
                logger.warning("Предыдущая задача сохранения конфигурации не завершилась в течение таймаута (%s секунд) после отмены. Возможно, она все еще выполняется.", config.debounce_save_delay + 1)
            except Exception as e:
                logger.error("Ошибка при отмене/завершении предыдущей задачи сохранения конфигурации: %s", e, exc_info=True)

        async def _save_task_coro():
            try:
                config = self.config_manager.get_config()
                logger.debug("Задача сохранения конфигурации: ожидание задержки %s секунд.", config.debounce_save_delay)
                await asyncio.sleep(config.debounce_save_delay)
                await self.config_manager.save_config()
                logger.info("Конфигурация успешно сохранена после задержки.")
            except asyncio.CancelledError:
                logger.debug("Задача сохранения конфигурации отменена.")
            except (OSError, TypeError, ValueError) as err:
                logger.error("Ошибка при отложенном сохранении конфигурации: %s", err, exc_info=True)
            except Exception as e:
                logger.error("Непредвиденная ошибка в задаче сохранения конфигурации: %s", e, exc_info=True)

        self._save_task = asyncio.create_task(_save_task_coro())

    async def cancel_pending_save_task(self) -> None:
        """
        Отменяет активную задачу сохранения конфигурации, если она существует и еще не завершена.
        Используется при завершении работы приложения для корректной очистки.
        """
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
            try:
                # Ожидаем завершения отмены с таймаутом
                logger.info("Ожидание завершения задачи сохранения конфигурации при завершении работы (таймаут 10 секунд).")
                await asyncio.wait_for(self._save_task, timeout=10.0)
                logger.info("Задача сохранения конфигурации завершена после отмены при завершении работы.")
            except asyncio.CancelledError:
                logger.info("Задача сохранения конфигурации отменена при завершении работы.")
            except asyncio.TimeoutError:
                logger.warning("Задача сохранения конфигурации не завершилась в течение 10 секунд после отмены при завершении работы. Возможно, она все еще выполняется.")
            except Exception as e:
                logger.error("Ошибка при отмене/завершении задачи сохранения конфигурации при завершении работы: %s", e, exc_info=True)

    async def manual_update(self) -> List[Dict[str, Any]]:
        """
        Запускает немедленное обновление списка инстансов.

        Этот метод используется API-эндпоинтом для принудительного обновления.

        Returns:
            List[Dict[str, Any]]: Обновленный список инстансов после завершения сканирования.
        """
        await self.perform_update()
        return await self.get_instances()
