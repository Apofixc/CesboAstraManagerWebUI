import json
import os
import sys
import logging

class ConfigManager:
    """
    Класс для управления загрузкой и валидацией конфигурации из JSON-файла.
    Если файл не существует, создается дефолтный.
    """

    def __init__(self, config_file_path=None, default_config=None):
        """
        Инициализация менеджера конфигурации.

        :param config_file_path: Путь к конфигурационному файлу (или None для дефолтных настроек).
        :param default_config: Словари дефолтных значений (если не задан, используется встроенный).
        """
        self.config_file_path = config_file_path
        self.default_config = default_config or self._get_default_config()
        self.config = self._load_config()

    def _get_default_config(self):
        """Возвращает дефолтную конфигурацию."""
        return {
            "host": "127.0.0.1",
            "start_port": 9200,
            "end_port": 9300,
            "servers": [],
            "check_interval": 300,
            "flask_host": "127.0.0.1",
            "flask_port": 5000,
            "debug": False,
            "scan_timeout": 5,
            "proxy_timeout": 15,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "screenshots_dir": "screenshots",
            "ffmpeg_timeout": 30
        }

    def _validate_servers(self, servers_list):
        """Валидирует список серверов."""
        valid_servers = []
        for srv in servers_list:
            if isinstance(srv, dict) and 'host' in srv and isinstance(srv['host'], str) and 'port' in srv and isinstance(srv['port'], int):
                valid_servers.append(srv)
            else:
                logging.warning(f"Пропущена недопустимая запись в servers: {srv}. Требуется {{'host': str, 'port': int}}")
        return valid_servers

    def _load_config(self):
        """
        Загружает и валидирует конфигурацию из файла или использует дефолтные настройки.
        Если файл не найден, создает его с дефолтными значениями.
        """
        if not self.config_file_path:
            logging.info("Путь к конфигурационному файлу не задан. Используем дефолтные настройки.")
            return self.default_config

        config_file_path = os.path.abspath(self.config_file_path)

        if not os.path.exists(config_file_path):
            try:
                with open(config_file_path, 'w') as f:
                    json.dump(self.default_config, f, indent=4)
                logging.info(f"Создан дефолтный конфиг-файл: {config_file_path}. Отредактируйте его и перезапустите приложение.")
                sys.exit(0)
            except OSError as e:
                logging.error(f"Ошибка создания файла {config_file_path}: {e}")
                return self.default_config

        try:
            with open(config_file_path, 'r') as f:
                loaded = json.load(f)
                
                # Начинаем с копии дефолтной конфигурации
                final_config = self.default_config.copy()

                # Перебираем загруженные данные и обновляем final_config только валидными значениями
                for key, value in loaded.items():
                    if key in final_config:
                        # Специальная обработка для списка servers
                        if key == 'servers':
                            validated_servers = self._validate_servers(value)
                            final_config[key] = validated_servers
                            if value and not validated_servers:
                                logging.warning("Все записи в servers недопустимы в файле, используем дефолт (пустой список).")
                        
                        # Обработка остальных ключей с проверкой типа
                        elif isinstance(value, type(final_config[key])):
                            final_config[key] = value
                        else:
                            logging.warning(f"Неверный тип для '{key}' в файле (ожидается {type(final_config[key]).__name__}). Используем дефолтное значение.")
                    else:
                        logging.debug(f"Неизвестный ключ '{key}' в файле конфигурации, игнорируем.")
                
                return final_config

        except json.JSONDecodeError:
            logging.exception(f"Ошибка чтения конфиг-файла {config_file_path}. Используем дефолтные значения.")
            return self.default_config
        except OSError:
            logging.exception(f"Ошибка доступа к файлу {config_file_path}. Используем дефолтные значения.")
            return self.default_config

    def get_config(self):
        """Возвращает текущую конфигурацию."""
        return self.config

    def reload_config(self):
        """Перезагружает конфигурацию (например, после изменения файла)."""
        self.config = self._load_config()