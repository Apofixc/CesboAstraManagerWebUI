"""
Модуль для управления медиапотоками и процессами.

Предоставляет класс MediaManager для запуска, остановки и мониторинга
процессов потоковой передачи HTTP-TS, а также для очистки завершенных процессов.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class MediaManager:
    """
    Класс для управления медиапотоками и связанными процессами.

    Отвечает за запуск и остановку процессов потоковой передачи (например, FFmpeg или VLC),
    мониторинг их состояния и очистку ресурсов.
    """

    def __init__(self, config=None):
        """
        Инициализирует MediaManager.

        Args:
            config (dict, optional): Словарь конфигурации, содержащий настройки,
                                     такие как 'monitor_interval', 'use_vlc', 'chunk_size'.
                                     По умолчанию пустой словарь.
        """
        self.config = config or {}
        self.active_processes = {}
        self.monitor_task = None
        self._start_monitor()
        logger.info("MediaManager инициализирован для потоковой передачи HTTP-TS.")

    def _start_monitor(self):
        """
        Запускает фоновую задачу мониторинга для очистки завершенных процессов.
        """
        interval = self.config.get('monitor_interval', 60)

        async def monitor_loop():
            """
            Бесконечный цикл мониторинга, периодически вызывающий очистку процессов.
            """
            while True:
                await self._cleanup_finished_processes()
                await asyncio.sleep(interval)
        self.monitor_task = asyncio.create_task(monitor_loop())

    async def _cleanup_finished_processes(self):
        """
        Очищает завершенные процессы потоковой передачи.

        Проверяет статус каждого активного процесса и удаляет его из списка,
        логируя результат завершения (успех или ошибка).
        """
        to_remove = []
        for channel_name, (process, task) in self.active_processes.items():
            if process is not None and isinstance(process, asyncio.subprocess.Process):
                if process.poll() is not None:
                    to_remove.append(channel_name)
                    if process.returncode != 0:
                        try:
                            _, stderr = await process.communicate()
                            logger.error("Процесс канала '%s' завершился с ошибкой: %s",
                                         channel_name, stderr.decode())
                        except Exception as err:  # W0718: Catching too general exception Exception
                            logger.error("Ошибка чтения stderr для '%s': %s", channel_name, str(err))
                    else:
                        logger.info("Процесс канала '%s' завершился успешно.", channel_name)
                    if task:
                        task.cancel()
        for ch_name in to_remove:
            del self.active_processes[ch_name]

    def _build_http_ts_cmd(self, addr: str) -> list[str]:
        """
        Формирует команду для запуска процесса потоковой передачи HTTP-TS.

        Args:
            addr (str): Адрес источника потока.

        Returns:
            list[str]: Список аргументов команды для subprocess.
        """
        use_vlc = self.config.get('use_vlc', False)  # Учитываем конфиг
        if use_vlc:
            return ['cvlc', addr, '--sout=#std{access=file,mux=ts,dst=-}', '--sout-keep']
        return ['ffmpeg', '-y', '-i', addr, '-c', 'copy', '-f', 'mpegts', 'pipe:1']

    async def start_stream_process(self, channel_name: str, addr: str):
        """
        Запускает процесс потоковой передачи для указанного канала.

        Если процесс для канала уже существует, он будет остановлен перед запуском нового.

        Args:
            channel_name (str): Уникальное имя канала.
            addr (str): Адрес источника потока.

        Returns:
            asyncio.subprocess.Process: Объект запущенного подпроцесса.
        """
        if channel_name in self.active_processes:
            await self.stop_stream_process(channel_name)
        cmd = self._build_http_ts_cmd(addr)
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.active_processes[channel_name] = (process, None)
        logger.info("Запущен процесс потоковой передачи для канала '%s' (PID: %s)",
                    channel_name, process.pid)
        return process

    async def stop_stream_process(self, channel_name: str):
        """
        Останавливает процесс потоковой передачи для указанного канала.

        Args:
            channel_name (str): Уникальное имя канала.
        """
        process_tuple = self.active_processes.get(channel_name)
        if process_tuple:
            process, task = process_tuple
            if process is not None and isinstance(process, asyncio.subprocess.Process):
                if process.poll() is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()
                        logger.warning("Принудительно остановлен процесс для канала '%s'.",
                                       channel_name)
                    if task:
                        task.cancel()
                logger.info("Остановлен процесс потоковой передачи для канала '%s'.", channel_name)
            del self.active_processes[channel_name]

    async def close(self):
        """
        Закрывает MediaManager, останавливая все активные процессы и монитор.
        """
        logger.info("Закрытие MediaManager...")
        if self.monitor_task:
            self.monitor_task.cancel()
        for channel_name in list(self.active_processes.keys()):
            await self.stop_stream_process(channel_name)
        logger.info("MediaManager закрыт.")

    async def _generate_stream_chunks_async(self, channel_name: str, process: asyncio.subprocess.Process):
        """
        Асинхронный генератор для чтения чанков из stdout процесса потоковой передачи.

        Args:
            channel_name (str): Имя канала.
            process (asyncio.subprocess.Process): Объект подпроцесса.

        Yields:
            bytes: Чанки данных из stdout процесса.
        """
        try:
            chunk_size = self.config.get('chunk_size', 65536)
            while True:
                # Используем AWAIT для асинхронного чтения из PIPE
                if process.stdout:
                    chunk = await process.stdout.read(chunk_size)
                else:
                    logger.error("stdout процесса для канала '%s' недоступен.", channel_name)
                    break
                if not chunk:
                    logger.info("Конец потока для канала '%s' — stdout закрыт.", channel_name)
                    break
                yield chunk
        except Exception as err:  # W0718: Catching too general exception Exception
            logger.error("Ошибка при потоковой передаче чанков для %s: %s", channel_name, err)
        finally:
            # Безопасное удаление из словаря
            if channel_name in self.active_processes:
                if process is not None and isinstance(process, asyncio.subprocess.Process):
                    if process.poll() is None:
                        process.terminate()
                del self.active_processes[channel_name]
            logger.info("Генератор потока для %s завершен. Процесс остановлен.", channel_name)
