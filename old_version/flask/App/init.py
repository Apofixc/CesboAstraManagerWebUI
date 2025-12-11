import logging
from flask import Flask # type: ignore
from flask_cors import CORS # type: ignore
from werkzeug.middleware.proxy_fix import ProxyFix # type: ignore

from App.config_manager import ConfigManager
from App.instance_manager import InstanceManager
from App.proxy_router import ProxyRouter
from App.api_router import ApiRouter
from App.error_handler import ErrorHandler

logger = logging.getLogger(__name__)

class Server:
    def __init__(self, config_path=None):
        """Инициализирует сервер с конфигурацией."""
        self.config_manager = ConfigManager(config_path)
        self.instance_manager = None
        self.proxy_router = None
        self.api_router = None
        self.error_handler = None        
        self.app = None
        self.cache = None

    def create_app(self):
        """Создаёт и конфигурирует Flask-приложение."""

        self.app = Flask(__name__, template_folder='templates')

        # Middleware
        CORS(self.app)
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=1, x_host=1)

        # Инициализация компонентов
        config_manager = self.config_manager
        self.instance_manager = InstanceManager(config_manager)
        self.proxy_router = ProxyRouter(config_manager, self.instance_manager)
        self.api_router = ApiRouter(self.instance_manager)

        # Регистрация blueprint'ов
        self.app.register_blueprint(self.api_router.get_blueprint())
        self.app.register_blueprint(self.proxy_router.get_blueprint())
        
        # Обработчик ошибок
        self.error_handler = ErrorHandler(self.app)

        logger.info("Сервер инициализирован.")
        return self.app

    def run(self):
        """Запускает сервер."""
        if not self.app or not self.instance_manager:
            raise RuntimeError("Приложение не создано. Сначала вызовите create_app().")

        # Запуск фонового потока для обновлений
        self.instance_manager.start_update_thread() 

        config = self.config_manager.get_config()
        logger.info(f"Запуск сервера на {config.get('flask_host', '0.0.0.0')}:{config.get('flask_port', 5000)}")
        self.app.run(
            host=config.get('flask_host', '0.0.0.0'),
            port=config.get('flask_port', 5000),
            debug=config.get('debug', False)
        )