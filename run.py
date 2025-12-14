"""
Основной скрипт запуска приложения Astra Web-UI.

Этот файл отвечает за инициализацию ядра приложения (AppCore),
конфигурацию логирования и предоставление экземпляра Quart-приложения
для запуска с помощью ASGI-сервера, такого как Uvicorn.
"""

import logging

# Импорт основного класса, управляющего зависимостями и конфигурацией приложения
from App.init import AppCore
from App.config_manager import ConfigManager # Импортируем ConfigManager
from quart import Quart # Импортируем Quart для аннотации типа

# Настройка базового уровня логирования и формата сообщений
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Создаем экземпляр ConfigManager
config_manager_instance = ConfigManager(config_file_path="config.json")
# Синхронно загружаем конфигурацию
initial_config = config_manager_instance.init_config()

# Инициализация ядра приложения с уже загруженным ConfigManager
app_core = AppCore(config_manager=config_manager_instance)

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
