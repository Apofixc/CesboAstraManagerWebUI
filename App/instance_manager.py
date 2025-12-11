import asyncio
import logging
import httpx # type: ignore
from asyncio import Lock
from asyncio import Event as AsyncEvent
from typing import List, Dict, Any, Optional

from App.config_manager import ConfigManager

logger = logging.getLogger(__name__)

class InstanceManager:
    """
    Класс для управления и мониторинга состояния экземпляров (инстансов) Astra.

    Отвечает за периодическое сканирование сети, проверку доступности инстансов,
    хранение их статуса и уведомление других частей приложения об изменениях.
    """

    def __init__(self, config_manager: ConfigManager, http_client: httpx.AsyncClient):
        """
        Инициализирует менеджер инстансов.

        Args:
            config_manager: Экземпляр ConfigManager для доступа к настройкам сканирования.
        """
        self.config_manager = config_manager
        self.http_client = http_client
        self.instances: List[Dict[str, Any]] = []
        # Асинхронная блокировка для безопасного доступа к self.instances
        self.instances_lock: Lock = Lock() 
        # Событие для оповещения подписчиков (например, SSE-клиентов) об обновлениях
        self.update_event: AsyncEvent = AsyncEvent() 

    async def check_instance_alive(self, host: str, port: int, scan_timeout: int) -> Optional[Dict[str, Any]]:
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
        try:
            res = await self.http_client.get(f'http://{host}:{port}/api/health', timeout=scan_timeout)
            if res.status_code == 200:
                try:
                    return res.json()
                except ValueError:
                    logger.warning(f"Неверный JSON-ответ от {addr}")
        except httpx.RequestError as e:
            logger.debug(f"Не удалось подключиться к {addr}: {e}")
        return None


    async def perform_update(self):
        """
        Асинхронно обновляет список активных инстансов Astra с параллельным сканированием.

        Метод запускает проверку всех сконфигурированных или сканируемых адресов
        параллельно с помощью `asyncio.gather`, обновляет внутреннее состояние `self.instances`
        и устанавливает `self.update_event` при обнаружении изменений.
        """
        if not self.config_manager:
            logger.error("config_manager не инициализирован")
            return

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
                 for srv_host, srv_port, srv_type in target_addresses]

        # Параллельное выполнение всех задач
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Обработка результатов
        for (srv_host, srv_port, srv_type), result in zip(target_addresses, results):
            addr = f'{srv_host}:{srv_port}'
            
            if isinstance(result, Exception) or result is None:
                # Сервер недоступен (ошибка подключения или таймаут)
                logger.debug(f"Сервер {addr}: оффлайн или ошибка: {result}")
                # Если сервер был известен ранее или он из списка (не автоскан), сохраняем его как Offline
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
                    'version': instance_data.get('version', 'unknown'), # type: ignore
                    'status': 'Online'
                }
                logger.info(f"Сервер {addr}: онлайн, версия {temp_instances[addr]['version']}")

        # Атомарное обновление instances
        async with self.instances_lock:
            self.instances[:] = [{'addr': addr, **data} for addr, data in temp_instances.items()]

        # Проверка на изменения для установки события update_event
        has_changed = False
        if len(temp_instances) != len(old_instances):
            has_changed = True
        else:
            for addr, new_data in temp_instances.items():
                old_data = old_instances.get(addr, {})
                if old_data.get('status') != new_data['status'] or old_data.get('version') != new_data.get('version'):
                    has_changed = True
                    break

        if has_changed:
            # Устанавливаем и сразу сбрасываем событие, чтобы разбудить ожидающие корутины
            self.update_event.set()
            self.update_event.clear()

        logger.info(f"Обновлено {len(self.instances)} инстансов")

    async def async_update_loop(self):
        """
        Асинхронный цикл бесконечного обновления инстансов с интервалом.

        Запускается как фоновая задача Quart и работает на протяжении всего
        времени жизни приложения, периодически вызывая `perform_update`.
        """
        if not self.config_manager:
            logger.error("config_manager не инициализирован, останавливаем цикл обновления.")
            return

        config = self.config_manager.get_config()
        check_interval = config.check_interval
        
        # Запускаем первое обновление сразу при старте цикла
        await self.perform_update() 

        while True:
            try:
                # Ожидание интервала перед следующим обновлением
                await asyncio.sleep(check_interval)
                await self.perform_update()
            except Exception as e:
                # Логирование ошибок цикла, чтобы он не прерывался полностью
                logger.error(f"Ошибка в цикле обновлений: {e}")
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

        Args:
            Копия списка всех отслеживаемых инстансов с их статусами и версиями.
        """
        async with self.instances_lock:
            return self.instances.copy()

    async def manual_update(self) -> List[Dict[str, Any]]:
        """
        Запускает немедленное обновление списка инстансов (используется API-эндпоинтом).

        Returns:
            Обновленный список инстансов после завершения сканирования.
        """
        await self.perform_update()
        return await self.get_instances()
