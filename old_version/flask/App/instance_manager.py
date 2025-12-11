import asyncio
import concurrent.futures
import logging
import requests
import threading
import time
from threading import Lock, Event

class InstanceManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.instances = []
        self.instances_lock = Lock()
        self.update_event = Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self.thread = None
        self.loop = None

    def check_instance_alive(self, host, port, scan_timeout):
        """
        Проверяет доступность одного экземпляра Astra.
        Синхронная функция, выполняемая в потоке из пула.
        """
        try:
            res = requests.get(f'http://{host}:{port}/api/instance', timeout=scan_timeout)
            if res.ok:
                data = res.json()
                return data
        except requests.exceptions.RequestException as e:
            logging.debug(f"Не удалось подключиться к {host}:{port}: {e}")
        return None


    async def perform_update(self):
        """
        Асинхронно обновляет список активных инстансов Astra с параллельным сканированием.
        """
        if not self.config_manager:
            logging.error("config_manager не инициализирован")
            return

        config = self.config_manager.get_config()
        with self.instances_lock:
            old_instances = {inst['addr']: inst for inst in self.instances}
        temp_instances = {}

        host = config.get('host', '127.0.0.1')
        start_port = config.get('start_port', 9200)
        end_port = config.get('end_port', 9300)
        servers = config.get('servers', [])
        scan_timeout = config.get('scan_timeout', 5)

        target_addresses = []
        if servers:
            target_addresses = [(srv['host'], srv['port'], 'list') for srv in servers]
        else:
            target_addresses = [(host, p, 'autoscan') for p in range(start_port, end_port + 1)]

        # loop = asyncio.get_event_loop() # Используем self.loop, который будет установлен
        loop = self.loop or asyncio.get_event_loop() # Запасной вариант, если self.loop еще не установлен

        tasks = [loop.run_in_executor(
            self.executor, self.check_instance_alive, srv_host, srv_port, scan_timeout
        ) for srv_host, srv_port, srv_type in target_addresses]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Обработка результатов с учётом старых данных
        for (srv_host, srv_port, srv_type), result in zip(target_addresses, results):
            addr = f'{srv_host}:{srv_port}'
            
            if isinstance(result, Exception) or result is None:
                # Сервер недоступен (ошибка подключения или таймаут)
                logging.debug(f"Сервер {addr}: оффлайн или ошибка: {result}")
                if addr in old_instances:
                    temp_instances[addr] = {
                        'version': old_instances[addr]['version'],
                        'status': 'Offline'
                    }
                elif srv_type != 'autoscan':
                    # Добавляем в список оффлайн, только если он был в конфиге явно, а не автосканом
                    temp_instances[addr] = {'version': "unknown", 'status': 'Offline'}
            else:
                # Успешный результат: result содержит JSON-данные от Astra
                instance_data = result
                temp_instances[addr] = {
                    'version': instance_data.get('version', 'unknown'), # type: ignore
                    'status': 'Online'
                }
                logging.info(f"Сервер {addr}: онлайн, версия {temp_instances[addr]['version']}")

        # Атомарное обновление instances
        with self.instances_lock:
            self.instances[:] = [{'addr': addr, **data} for addr, data in temp_instances.items()]

        # Проверка на изменения для установки события
        flag = False
        with self.instances_lock:
            for addr, new_data in temp_instances.items():
                old_data = old_instances.get(addr, {})
                if old_data.get('status') != new_data['status'] or addr not in old_instances:
                    flag = True
                    break
            if len(temp_instances) != len(old_instances):
                flag = True

        if flag:
            self.update_event.set()
            # self.update_event.clear() # Это действие лучше оставить в получателю события

        logging.info(f"Обновлено {len(self.instances)} инстансов")

    async def async_update_loop(self):
        """
        Асинхронный цикл бесконечного обновления инстансов с интервалом.
        """
        if not self.config_manager:
            logging.error("config_manager не инициализирован, останавливаем цикл")
            return

        config = self.config_manager.get_config()
        check_interval = config.get('check_interval', 300)
        while True:
            try:
                await self.perform_update()
                await asyncio.sleep(check_interval)
            except Exception as e:
                logging.error(f"Ошибка в цикле обновлений: {e}")
                await asyncio.sleep(check_interval)  # Даже в случае ошибки ждём перед следующей попыткой


    def start_update_thread(self):
        """
        Запускает фоновый поток с асинхронным циклом обновлений.
        """
        if self.thread is None:
            def run():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.run_until_complete(self.async_update_loop())

            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()

    def check_instance_online(self, addr):
        """Вспомогательная функция для проверки, онлайн ли инстанс."""
        with self.instances_lock:
            return any(i['addr'] == addr and i['status'] == 'Online' for i in self.instances)

    def get_instances(self):
        """Возвращает копию списка инстансов."""
        with self.instances_lock:
            return self.instances.copy()

    def manual_update(self):
        """
        Выполняет ручное обновление асинхронно (для API) и возвращает текущий список.
        """
        if self.loop and self.loop.is_running():
            if not self.config_manager:
                logging.error("config_manager не инициализирован")
                return

            config = self.config_manager.get_config()
            # Добавляем корутину в фоновый loop и блокируем текущий поток до завершения
            future = asyncio.run_coroutine_threadsafe(self.perform_update(), self.loop)
            try:
                # Ждем результата с таймаутом, чтобы не зависнуть навсегда
                future.result(timeout=config.get('proxy_timeout', 15) * 2) 
            except concurrent.futures.TimeoutError:
                logging.error("Таймаут ручного обновления")
            except Exception as e:
                logging.error(f"Ошибка в ручном обновлении: {e}")
        else:
             logging.warning("Попытка ручного обновления до запуска фонового цикла.")
        # --------------------------------------------------------------------------------

        return self.get_instances()
