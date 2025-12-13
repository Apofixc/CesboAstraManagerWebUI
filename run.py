"""
Основной скрипт запуска приложения Astra Web-UI.

Этот файл отвечает за инициализацию ядра приложения (AppCore),
конфигурацию логирования и предоставление экземпляра Quart-приложения
для запуска с помощью ASGI-сервера, такого как Uvicorn.
"""

import logging
import asyncio # Добавляем импорт asyncio

# Импорт основного класса, управляющего зависимостями и конфигурацией приложения
from App.init import AppCore 

# Настройка базового уровня логирования и формата сообщений
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Определяем асинхронную функцию для инициализации приложения
async def main():
    try:
        # Инициализация ядра приложения с указанием пути к файлу конфигурации
        app_core = AppCore(config_path="config.json")
        await app_core.async_init() # Вызываем асинхронную инициализацию

        # Создание и конфигурирование экземпляра Quart-приложения
        app = app_core.create_app() 
        return app
    except KeyboardInterrupt:
        logger.info("Инициализация приложения прервана пользователем")
        return None
    except Exception as e:
        logger.error(f"Ошибка инициализации: {str(e)}")
        return None

# Переменная app должна быть доступна для Uvicorn
app = None

if __name__ == '__main__':
    logger.info("Приложение инициализировано. Запуск через команду Uvicorn.")
    logger.info("Запустите сервер командой: uvicorn run:app --reload")
    # Запускаем асинхронную функцию main и присваиваем результат глобальной переменной app
    app = asyncio.run(main())
