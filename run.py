"""
Основной скрипт запуска приложения Astra Web-UI.

Этот файл отвечает за инициализацию ядра приложения (AppCore),
конфигурацию логирования и предоставление экземпляра Quart-приложения
для запуска с помощью ASGI-сервера, такого как Uvicorn.
"""

import logging
import asyncio

# Импорт основного класса, управляющего зависимостями и конфигурацией приложения
from .App.init import AppCore

# Настройка базового уровня логирования и формата сообщений
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация ядра приложения с указанием пути к файлу конфигурации
app_core = AppCore(config_path="config.json")

def app():
    """
    Фабричная функция для Uvicorn, которая асинхронно создает и возвращает
    экземпляр Quart-приложения.
    """
    try:
        return app_core.create_app()
    except KeyboardInterrupt:
        logger.info("Инициализация приложения прервана пользователем")
        raise
    except Exception as e:
        logger.error(f"Ошибка инициализации: {str(e)}")
        raise

if __name__ == '__main__':
    logger.info("Приложение инициализировано. Запуск через команду Uvicorn.")
    logger.info("Запустите сервер командой: uvicorn astra_manager.run:app --reload --factory")
