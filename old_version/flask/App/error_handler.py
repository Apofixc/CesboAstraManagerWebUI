import logging
from flask import jsonify # type: ignore

logger = logging.getLogger(__name__)

class ErrorHandler:
    def __init__(self, app):
        self.app = app
        self.register_error_handlers()

    def register_error_handlers(self):
        """Регистрирует глобальные обработчики ошибок."""
        @self.app.errorhandler(404)
        def handle_404(error):
            logger.warning(f"404 ошибка: {error}")
            return jsonify({"error": "Not found", "message": str(error)}), 404

        @self.app.errorhandler(500)
        def handle_500(error):
            logger.error(f"500 ошибка (внутренняя ошибка сервера): {error}")
            return jsonify({"error": "Internal server error", "message": "Something went wrong"}), 500

        @self.app.errorhandler(400)
        def handle_400(error):
            logger.info(f"400 ошибка (плохой запрос): {error}")
            return jsonify({"error": "Bad request", "message": str(error)}), 400

        @self.app.errorhandler(403)
        def handle_403(error):
            logger.warning(f"403 ошибка (запрещено): {error}")
            return jsonify({"error": "Forbidden", "message": str(error)}), 403

        @self.app.errorhandler(Exception)
        def handle_generic_exception(error):
            logger.error(f"Необработанная ошибка: {error}", exc_info=True)
            return jsonify({"error": "Unexpected error", "message": "An unexpected error occurred."}), 500

    def add_custom_error_handler(self, code, handler_func):
        """Метод для добавления кастомных обработчиков ошибок, если нужно."""
        @self.app.errorhandler(code)
        def custom_handler(error):
            return handler_func(error)
