"""
Модуль для централизованной настройки логирования в приложении Astra Web-UI.

Предоставляет функцию для конфигурирования базового логирования,
которая может быть вызвана один раз при старте приложения.
"""
import logging
from typing import Optional

def setup_logging(debug: bool = False, log_file: Optional[str] = None):
    """
    Настраивает базовое логирование для приложения.

    Args:
        debug (bool): Если True, уровень логирования устанавливается в DEBUG, иначе в INFO.
                      По умолчанию False.
        log_file (Optional[str]): Путь к файлу для записи логов. Если None, логи
                                  выводятся в консоль.
    """
    level = logging.DEBUG if debug else logging.INFO

    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    # Устанавливаем уровень для httpx, чтобы избежать слишком подробных логов от него
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Логирование настроено на уровень %s", logging.getLevelName(level))
    if log_file:
        logger.info("Логи будут записываться в файл: %s", log_file)
