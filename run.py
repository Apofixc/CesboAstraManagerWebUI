"""
Основной скрипт запуска приложения Astra Web-UI.

Этот файл отвечает за инициализацию ядра приложения (AppCore),
конфигурацию логирования и предоставление экземпляра Quart-приложения
для запуска с помощью ASGI-сервера, такого как Uvicorn.
"""

import logging
from quart import Quart # type: ignore

from astra_manager.App.init import AppCore
from astra_manager.App.logger_config import setup_logging

# Настройка базового уровня логирования и формата сообщений
setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация ядра приложения с уже загруженным ConfigManager
app_core = AppCore(config_path="config.json")

def app() -> Quart: # Указываем тип возвращаемого значения
    """
    Создает и возвращает экземпляр Quart-приложения для Uvicorn.

    Эта фабричная функция асинхронно инициализирует ядро приложения
    и предоставляет готовый экземпляр Quart для запуска ASGI-сервером.
    """
    try:
        return app_core.create_app()
    except KeyboardInterrupt:
        logger.info("Инициализация приложения прервана пользователем")
        raise
    except Exception as e:
        logger.error("Ошибка инициализации: %s", e)
        raise

if __name__ == '__main__':
    logger.info("Приложение инициализировано. Запуск через команду Uvicorn.")
    logger.info("Запустите сервер командой: uvicorn astra_manager.run:app --reload --factory")
