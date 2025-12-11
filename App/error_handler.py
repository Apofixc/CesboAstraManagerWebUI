import logging
from quart import jsonify, Quart, Response # type: ignore
from werkzeug.exceptions import HTTPException # type: ignore
from typing import Tuple

logger = logging.getLogger(__name__)

class ErrorHandler:
    """
    Класс для централизованной обработки HTTP-ошибок и исключений в приложении Quart.

    Предоставляет асинхронные обработчики для стандартных кодов ошибок (400, 403, 404, 500)
    и регистрирует их в основном приложении.
    """

    def __init__(self, app: Quart):
        """
        Инициализирует обработчик ошибок и регистрирует его методы в приложении.

        Args:
            app: Экземпляр приложения Quart, в котором будут зарегистрированы обработчики.
        """
        self.app = app

        self.register_error_handlers(app) 
        logger.info("Класс ErrorHandler инициализирован и обработчики зарегистрированы.")

    async def handle_404(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 404 (Not Found).

        Логирует ошибку и возвращает JSON-ответ с соответствующим статусом.

        Args:
            error: Объект исключения HTTPException.

        Returns:
            Кортеж (JSON-ответ, HTTP-статус 404).
        """
        logger.warning(f"404 ошибка: {error}")
        return jsonify({"error": "Not found", "message": str(error)}), 404

    async def handle_500(self, error: Exception) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 500 (Internal Server Error) или общее исключение.

        Логирует критическую ошибку и возвращает общий JSON-ответ для клиента.

        Args:
            error: Объект исключения.

        Returns:
            Кортеж (JSON-ответ, HTTP-статус 500).
        """
        logger.error(f"500 ошибка (внутренняя ошибка сервера): {error}", exc_info=True)
        return jsonify({"error": "Internal server error", "message": "Something went wrong"}), 500

    async def handle_400(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 400 (Bad Request).

        Логирует ошибку запроса и возвращает JSON-ответ с деталями.

        Args:
            error: Объект исключения HTTPException.

        Returns:
            Кортеж (JSON-ответ, HTTP-статус 400).
        """
        logger.info(f"400 ошибка (плохой запрос): {error}")
        return jsonify({"error": "Bad request", "message": str(error)}), 400

    async def handle_403(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 403 (Forbidden).

        Логирует ошибку доступа и возвращает JSON-ответ.

        Args:
            error: Объект исключения HTTPException.

        Returns:
            Кортеж (JSON-ответ, HTTP-статус 403).
        """
        logger.warning(f"403 ошибка (запрещено): {error}")
        return jsonify({"error": "Forbidden", "message": str(error)}), 403

    async def handle_generic_exception(self, error: Exception) -> Tuple[Response, int]:
        """
        Обрабатывает любые необработанные исключения, которые не являются HTTP-ошибками.

        Логирует полную информацию об исключении и возвращает JSON-ответ с HTTP-статусом 500.

        Args:
            error: Объект исключения.

        Returns:
            Кортеж (JSON-ответ, HTTP-статус 500).
        """
        logger.error(f"Необработанная ошибка: {error}", exc_info=True)
        return jsonify({"error": "Unexpected error", "message": "An unexpected error occurred."}), 500

    def add_custom_error_handler(self, code: int, handler_func):
        """
        Метод для динамического добавления кастомных обработчиков ошибок во время выполнения.

        Args:
            code: HTTP-код ошибки (например, 405).
            handler_func: Асинхронная функция-обработчик, принимающая объект ошибки.
        """
        @self.app.errorhandler(code)
        async def custom_handler(error):
            return await handler_func(error)

    def register_error_handlers(self, app: Quart):
        """
        Регистрирует методы текущего класса как глобальные обработчики ошибок в приложении Quart.

        Args:
            app: Экземпляр приложения Quart.
        """
        # Используем self.handle_... для регистрации методов текущего объекта
        app.register_error_handler(404, self.handle_404)
        app.register_error_handler(500, self.handle_500)
        app.register_error_handler(400, self.handle_400)
        app.register_error_handler(403, self.handle_403)
        # Обработка любых других исключений как 500
        app.register_error_handler(Exception, self.handle_generic_exception)