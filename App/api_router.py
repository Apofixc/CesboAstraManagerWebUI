"""
Модуль для маршрутизации API-запросов в приложении Astra Web-UI.

Определяет эндпоинты, обрабатывает HTTP-запросы, рендерит UI
и обеспечивает потоковую передачу данных через Server-Sent Events (SSE).
"""
import asyncio
import json
import logging
from typing import Tuple

from quart import Blueprint, jsonify, render_template, Response  # type: ignore

from App.instance_manager import InstanceManager

logger = logging.getLogger(__name__)


class ApiRouter:
    """
    Класс маршрутизатора API для интеграции с Quart приложением.

    Отвечает за определение эндпоинтов (endpoints), обработку HTTP-запросов,
    рендеринг UI и потоковую передачу данных через Server-Sent Events (SSE).
    """

    def __init__(self, instance_manager: InstanceManager):
        """
        Инициализирует маршрутизатор API.

        Args:
            instance_manager (InstanceManager): Экземпляр класса InstanceManager,
                                                предоставляющий методы для работы с данными.
        """
        self.instance_manager = instance_manager
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
            try:
                while True:
                    # Ожидание сигнала о новых данных от менеджера инстансов
                    await self.instance_manager.update_event.wait()

                    # Получение актуальных данных
                    data = await self.instance_manager.get_instances()

                    if last_sent != data:
                        # Используйте DEBUG вместо INFO для частых сообщений
                        logger.debug("Отправка обновления инстансов через SSE")
                        yield f"data: {json.dumps(data)}\n\n"
                        last_sent = data

            except asyncio.CancelledError:
                # Ожидаемое исключение при закрытии соединения клиентом (браузером)
                logger.info("SSE-соединение для /api/instances отменено клиентом.")
            except Exception as err:
                logger.error("Критическая ошибка в SSE-генераторе: %s", err, exc_info=True)
                # Отправка сообщения об ошибке клиенту SSE
                error_message = json.dumps({
                    'error': 'Ошибка в потоке обновлений',
                    'message': 'Произошла непредвиденная ошибка.'
                })
                yield f"event: error\ndata: {error_message}\n\n"
            finally:
                logger.debug("Завершение работы SSE-генератора.")

        return Response(generate(), mimetype='text/event-stream')

    async def api_update_instances(self) -> Response:
        """
        Обработчик POST-запроса для URL '/api/update_instances'.

        Запускает принудительное ручное обновление списка инстансов через InstanceManager.

        Returns:
            Response: JSON-ответ, содержащий текущее состояние инстансов после обновления.
        """
        data = await self.instance_manager.manual_update()
        return jsonify(data)

    def get_blueprint(self) -> Blueprint:
        """
        Возвращает сконфигурированный объект Blueprint.

        Этот Blueprint содержит все зарегистрированные маршруты и готов
        к регистрации в основном приложении Quart.

        Returns:
            Blueprint: Экземпляр Quart Blueprint с зарегистрированными маршрутами.
        """
        return self.blueprint
