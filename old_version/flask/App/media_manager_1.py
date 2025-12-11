import logging
import asyncio
import os
import shutil
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class MediaManager:
    def __init__(self, config=None):
        self.config = config or {}
        self.active_processes = {}  
        self.monitor_task = None
        self._start_monitor() 
        logger.info("MediaManager инициализирован для потоковой передачи HTTP-TS.")

    def _start_monitor(self):
        interval = self.config.get('monitor_interval', 60)
        async def monitor_loop():
            while True:
                await self._cleanup_finished_processes()
                await asyncio.sleep(interval)
        self.monitor_task = asyncio.create_task(monitor_loop())

    async def _cleanup_finished_processes(self):
        to_remove = []
        for channel_name, (process, task) in self.active_processes.items():
            if process.poll() is not None:
                to_remove.append(channel_name)
                if process.returncode != 0:
                    try:
                        _, stderr = await process.communicate()
                        logger.error(f"Процесс канала '{channel_name}' завершился с ошибкой: {stderr.decode()}")
                    except Exception as e:
                        logger.error(f"Ошибка чтения stderr для '{channel_name}': {str(e)}")
                else:
                    logger.info(f"Процесс канала '{channel_name}' завершился успешно.")
                if task: task.cancel()
        for ch in to_remove:
            del self.active_processes[ch]

    def _build_http_ts_cmd(self, addr, use_vlc=False):
        use_vlc = self.config.get('use_vlc', False) # Учитываем конфиг
        if use_vlc:
             return ['cvlc', addr, '--sout=#std{access=file,mux=ts,dst=-}', '--sout-keep']
        else:
            return ['ffmpeg', '-y', '-i', addr, '-c', 'copy', '-f', 'mpegts', 'pipe:1']

    async def start_stream_process(self, channel_name, addr):
        if channel_name in self.active_processes:
            await self.stop_stream_process(channel_name)
        cmd = self._build_http_ts_cmd(addr)
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.active_processes[channel_name] = (process, None)
        logger.info(f"Запущен процесс потоковой передачи для канала '{channel_name}' (PID: {process.pid})")
        return process

    async def stop_stream_process(self, channel_name):
        process_tuple = self.active_processes.get(channel_name)
        if process_tuple:
            process, task = process_tuple
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    logger.warning(f"Принудительно остановлен процесс для канала '{channel_name}'.")
                if task: task.cancel()
            logger.info(f"Остановлен процесс потоковой передачи для канала '{channel_name}'.")
        if channel_name in self.active_processes:
            del self.active_processes[channel_name]

    async def close(self):
        logger.info("Закрытие MediaManager...")
        if self.monitor_task: self.monitor_task.cancel()
        for channel_name in list(self.active_processes.keys()):
            await self.stop_stream_process(channel_name)
        logger.info("MediaManager закрыт.")

    # Используем этот синхронный генератор в хендлере Flask
    def _generate_stream_chunks_sync(self, channel_name, process):
        try:
            chunk_size = self.config.get('chunk_size', 65536)
            while True:
                chunk = process.stdout.read(chunk_size)
                if not chunk:
                    logger.info(f"Конец потока для канала '{channel_name}' — stdout закрыт.")
                    break
                yield chunk
        except Exception as e:
            logger.error(f"Ошибка при потоковой передаче чанков для {channel_name}: {e}")
        finally:
            if process.poll() is None:
                process.terminate()
            logger.info(f"Генератор потока для {channel_name} завершен. Процесс остановлен.")
            if channel_name in self.active_processes:
                 del self.active_processes[channel_name]
