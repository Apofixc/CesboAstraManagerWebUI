"""
Основной скрипт запуска приложения Astra Web-UI.

Этот файл отвечает за инициализацию ядра приложения (AppCore),
конфигурацию логирования и предоставление экземпляра Quart-приложения
для запуска с помощью ASGI-сервера, такого как Uvicorn.
"""

import logging
from quart import Quart # type: ignore

from App.init import AppCore
from App.logger_config import setup_logging
from App.config_manager import ConfigManager
from App.lifecycle_manager import LifecycleManager

# Настройка базового уровня логирования и формата сообщений
# Временно устанавливаем debug=False, так как конфигурация еще не загружена.
setup_logging(debug=False)
logger = logging.getLogger(__name__)

# Инициализация менеджеров
config_manager = ConfigManager(config_file_path="config.json")
# LifecycleManager инициализируется без app и sse_tasks, они будут установлены позже
lifecycle_manager = LifecycleManager(app=None, config_manager=config_manager, sse_tasks=set()) # type: ignore

# Инициализация ядра приложения с внедрением зависимостей
app_core = AppCore(config_manager=config_manager, lifecycle_manager=lifecycle_manager)

async def _async_init_app_core_and_logging():
    """Асинхронно инициализирует AppCore и перенастраивает логирование."""
    await app_core.config_manager.async_init()
    config = app_core.config_manager.get_config()
    setup_logging(debug=config.debug) # Перенастраиваем логирование с учетом значения debug из конфига
    logger.info("Логирование перенастроено с учетом конфигурации (debug=%s).", config.debug)

def app() -> Quart: # Указываем тип возвращаемого значения
    """
    Создает и возвращает экземпляр Quart-приложения для Uvicorn.

    Эта фабричная функция асинхронно инициализирует ядро приложения
    и предоставляет готовый экземпляр Quart для запуска ASGI-сервером.
    """
    try:
        # Запускаем асинхронную инициализацию AppCore и логирования
        app_core.app.before_serving(_async_init_app_core_and_logging)
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
