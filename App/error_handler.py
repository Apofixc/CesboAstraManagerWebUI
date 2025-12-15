"""
Модуль для централизованной обработки HTTP-ошибок и исключений в приложении Quart.

Предоставляет асинхронные обработчики для стандартных кодов ошибок (400, 403, 404, 500)
и регистрирует их в основном приложении.
"""
from typing import Tuple
import logging

from quart import jsonify, Quart, Response  # type: ignore
from werkzeug.exceptions import HTTPException  # type: ignore
from pydantic import ValidationError # type: ignore

from .api_models import ErrorResponse # Импорт Pydantic модели для ошибок

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
            app (Quart): Экземпляр приложения Quart, в котором будут зарегистрированы обработчики.
        """
        self.app = app

        self.register_error_handlers(app)
        logger.info("Класс ErrorHandler инициализирован и обработчики зарегистрированы.")

    async def handle_404(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 404 (Not Found).

        Логирует ошибку и возвращает JSON-ответ с соответствующим статусом.

        Args:
            error (HTTPException): Объект исключения HTTPException.

        Returns:
            Tuple[Response, int]: Кортеж (JSON-ответ, HTTP-статус 404).
        """
        logger.warning("404 ошибка: %s", error)
        error_response = ErrorResponse(error="Not Found", message=str(error), details=error.description if isinstance(error, HTTPException) else None)
        return jsonify(error_response.model_dump()), 404

    async def handle_400(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 400 (Bad Request).

        Логирует ошибку запроса и возвращает JSON-ответ с деталями.

        Args:
            error (HTTPException): Объект исключения HTTPException.

        Returns:
            Tuple[Response, int]: Кортеж (JSON-ответ, HTTP-статус 400).
        """
        logger.warning("400 ошибка (плохой запрос): %s", error)
        error_response = ErrorResponse(error="Bad Request", message=str(error), details=error.description if isinstance(error, HTTPException) else None)
        return jsonify(error_response.model_dump()), 400

    async def handle_403(self, error: HTTPException) -> Tuple[Response, int]:
        """
        Обрабатывает HTTP-ошибку 403 (Forbidden).

        Логирует ошибку доступа и возвращает JSON-ответ.

        Args:
            error (HTTPException): Объект исключения HTTPException.

        Returns:
            Tuple[Response, int]: Кортеж (JSON-ответ, HTTP-статус 403).
        """
        logger.warning("403 ошибка (запрещено): %s", error)
        error_response = ErrorResponse(error="Forbidden", message=str(error), details=error.description if isinstance(error, HTTPException) else None)
        return jsonify(error_response.model_dump()), 403

    async def handle_generic_exception(self, error: Exception) -> Tuple[Response, int]:
        """
        Обрабатывает любые необработанные исключения, не являющиеся HTTP-ошибками.

        Логирует полную информацию об исключении и возвращает JSON-ответ
        с HTTP-статусом 500.

        Args:
            error (Exception): Объект исключения.

        Returns:
            Tuple[Response, int]: Кортеж (JSON-ответ, HTTP-статус 500).
        """
        logger.error("Необработанная ошибка: %s", error, exc_info=True)
        error_response = ErrorResponse(error="Internal Server Error", message="An unexpected error occurred.", details=str(error))
        return jsonify(error_response.model_dump()), 500

    def add_custom_error_handler(self, code: int, handler_func):
        """
        Динамически добавляет кастомные обработчики ошибок во время выполнения.

        Args:
            code (int): HTTP-код ошибки (например, 405).
            handler_func (Callable): Асинхронная функция-обработчик,
                                     принимающая объект ошибки.
        """
        @self.app.errorhandler(code)
        async def custom_handler(error):
            return await handler_func(error)

    def register_error_handlers(self, app: Quart):
        """
        Регистрирует методы текущего класса как глобальные обработчики ошибок.

        Args:
            app (Quart): Экземпляр приложения Quart.
        """
        # Используем self.handle_... для регистрации методов текущего объекта
        app.register_error_handler(404, self.handle_404)
        app.register_error_handler(400, self.handle_400)
        app.register_error_handler(403, self.handle_403)
        # Обработка любых других исключений как 500
        app.register_error_handler(500, self.handle_generic_exception)
        app.register_error_handler(Exception, self.handle_generic_exception)
