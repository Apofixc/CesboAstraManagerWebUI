"""
Модуль для маршрутизации API-запросов в приложении Astra Web-UI.

Определяет эндпоинты, обрабатывает HTTP-запросы, рендерит UI
и обеспечивает потоковую передачу данных через Server-Sent Events (SSE).
"""
import asyncio
import json
from typing import Any, Tuple
import logging

from quart import Blueprint, jsonify, render_template, Response, request # type: ignore
from pydantic import ValidationError # type: ignore

from .instance_manager import InstanceManager
from .api_models import AstraAddrRequest # Импорт Pydantic модели

logger = logging.getLogger(__name__)


class ApiRouter:
    """
    Класс маршрутизатора API для интеграции с Quart приложением.

    Отвечает за определение эндпоинтов (endpoints), обработку HTTP-запросов,
    рендеринг UI и потоковую передачу данных через Server-Sent Events (SSE).
    """

    def __init__(self, instance_manager: InstanceManager, app_core: Any):
        """
        Инициализирует маршрутизатор API.

        Args:
            instance_manager (InstanceManager): Экземпляр класса InstanceManager,
                                                предоставляющий методы для работы с данными.
            app_core (Any): Экземпляр AppCore для доступа к _sse_tasks.
        """
        self.instance_manager = instance_manager
        self.app_core = app_core # Сохраняем ссылку на AppCore
        # Инициализация blueprint с указанием пути к шаблонам
        self.blueprint = Blueprint('api', __name__, template_folder='../templates')
        self.setup_routes()

    def setup_routes(self):
        """
        Регистрирует URL-маршруты и соответствующие им асинхронные обработчики.

        Метод добавляет правила URL и связывает их с функциями-обработчиками
        (view functions) в blueprint.
        """
        self.blueprint.add_url_rule('/', 'index', self.index)
        self.blueprint.add_url_rule('/api/instances', 'get_instances', self.get_instances)
        self.blueprint.add_url_rule('/api/update_instances', 'api_update_instances_route',
                                    self.api_update_instances, methods=['POST'])

    async def index(self) -> Tuple[Response, int]:
        """
        Обработчик корневого URL '/'.

        Рендерит основной HTML-шаблон пользовательского интерфейса приложения.

        Returns:
            Tuple[Response, int]: Ответ с отрендеренным содержимым файла index.html
                                  и HTTP-статусом 200.
        """
        return Response(await render_template('index.html')), 200

    async def get_instances(self) -> Response:
        """
        Обработчик URL '/api/instances' для Server-Sent Events (SSE).

        Устанавливает SSE-соединение для потоковой передачи обновлений списка
        инстансов в реальном времени клиенту.

        Returns:
            Response: Объект Quart Response с mimetype='text/event-stream'.
        """
        async def generate():
            last_sent = None
            current_task = asyncio.current_task()
            if current_task:
                self.app_core.add_sse_task(current_task)
                logger.info("SSE-генератор запущен для нового клиента. Задача добавлена в отслеживание: %s",
                            current_task.get_name())
            else:
                logger.warning("Не удалось получить текущую задачу SSE-генератора.")

            try:
                while True:
                    # Ожидание сигнала о новых данных от менеджера инстансов с таймаутом
                    try:
                        await asyncio.wait_for(self.instance_manager.update_event.wait(), timeout=1.0)
                        self.instance_manager.update_event.clear() # Сбрасываем событие после ожидания
                        logger.debug("SSE-генератор: получено событие обновления.")
                    except asyncio.TimeoutError:
                        # Таймаут истек, продолжаем цикл для проверки отмены
                        logger.debug("SSE-генератор: таймаут ожидания события, проверка отмены.")

                    # Получение актуальных данных
                    data = await self.instance_manager.get_instances()

                    if last_sent != data:
                        logger.debug("SSE-генератор: обнаружены новые данные, отправка обновления.")
                        try:
                            json_data = json.dumps(data)
                            yield f"data: {json_data}\n\n"
                            last_sent = data
                        except TypeError as json_err:
                            logger.error("Ошибка сериализации JSON в SSE-генераторе: %s", json_err, exc_info=True)
                            error_message = json.dumps({
                                'error': 'Ошибка сериализации данных',
                                'message': 'Не удалось преобразовать данные в JSON.'
                            })
                            yield f"event: error\ndata: {error_message}\n\n"
                    else:
                        logger.debug("SSE-генератор: данные не изменились.")

            except asyncio.CancelledError:
                # Ожидаемое исключение при закрытии соединения клиентом (браузером или Uvicorn)
                logger.info("SSE-соединение для /api/instances отменено.")
            except RuntimeError as e:
                logger.error("Непредвиденная ошибка в SSE-генераторе: %s", e, exc_info=True)
            finally:
                if current_task:
                    self.app_core.remove_sse_task(current_task)
                    logger.info("SSE-генератор завершен. Задача удалена из отслеживания: %s", current_task.get_name())
                else:
                    logger.debug("SSE-генератор завершен (задача не была в отслеживании или не найдена).")

        response = Response(generate(), mimetype='text/event-stream')
        # Добавляем заголовок Connection: close, чтобы явно указать клиенту закрыть соединение
        response.headers['Connection'] = 'close'
        return response

    async def api_update_instances(self) -> Tuple[Response, int]:
        """
        Обработчик POST-запроса для URL '/api/update_instances'.

        Запускает принудительное ручное обновление списка инстансов через InstanceManager.

        Returns:
            Response: JSON-ответ, содержащий текущее состояние инстансов после обновления.
        """
        try:
            # Валидация запроса не требуется для этого эндпоинта, так как он не принимает тело запроса
            data = await self.instance_manager.manual_update()
            return jsonify(data), 200
        except ValidationError as e:
            logger.warning("Ошибка валидации запроса для /api/update_instances: %s", e.errors())
            return jsonify({'error': 'Ошибка валидации запроса', 'details': e.errors()}), 400
        except Exception as e:
            logger.error("Непредвиденная ошибка в api_update_instances: %s", e, exc_info=True)
            return jsonify({'error': 'Непредвиденная ошибка сервера'}), 500

    def get_blueprint(self) -> Blueprint:
        """
        Возвращает сконфигурированный объект Blueprint.

        Этот Blueprint содержит все зарегистрированные маршруты и готов
        к регистрации в основном приложении Quart.

        Returns:
            Blueprint: Экземпляр Quart Blueprint с зарегистрированными маршрутами.
        """
        return self.blueprint
