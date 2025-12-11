import json
import logging
from quart import Blueprint, render_template, Response, jsonify, request # type: ignore
import asyncio

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
            instance_manager: Экземпляр класса InstanceManager, предоставляющий методы для работы с данными.
        """
        self.instance_manager = instance_manager
        # Инициализация blueprint с указанием пути к шаблонам
        self.blueprint = Blueprint('api', __name__, template_folder='../templates')
        self.setup_routes()

    def setup_routes(self):
        """
        Регистрирует URL-маршруты и соответствующие им асинхронные обработчики (view functions) в blueprint.
        """
        self.blueprint.add_url_rule('/', 'index', self.index)
        self.blueprint.add_url_rule('/api/instances', 'get_instances', self.get_instances)
        self.blueprint.add_url_rule('/api/update_instances', 'api_update_instances', self.api_update_instances, methods=['POST'])

    async def index(self) -> str:
        """
        Обработчик корневого URL '/'.

        Рендерит основной HTML-шаблон пользовательского интерфейса приложения.

        Returns:
            Ответ с отрендеренным содержимым файла index.html.
        """
        return await render_template('index.html')

    async def get_instances(self) -> Response:
        """
        Обработчик URL '/api/instances'.

        Устанавливает соединение Server-Sent Events (SSE) для потоковой передачи
        обновлений списка инстансов в реальном времени клиенту.

        Returns:
            Quart Response объект с mimetype='text/event-stream'.
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
            except Exception as e:
                # Логирование любых других неожиданных ошибок
                logger.error(f"Критическая ошибка в SSE-генераторе: {e}", exc_info=True)
            finally:
                # Этот блок гарантирует, что генератор завершится корректно
                logger.debug("Завершение работы SSE-генератора.")

        return Response(generate(), mimetype='text/event-stream')

    async def api_update_instances(self) -> Response:
        """
        Обработчик URL '/api/update_instances' (POST метод).

        Запускает принудительное ручное обновление списка инстансов через InstanceManager.

        Returns:
            JSON-ответ, содержащий текущее состояние инстансов после обновления.
        """
        data = await self.instance_manager.manual_update()
        return jsonify(data)
    
    def get_blueprint(self) -> Blueprint:
        """
        Возвращает сконфигурированный объект Blueprint для регистрации в основном приложении Quart.

        Returns:
            Экземпляр Quart Blueprint с зарегистрированными маршрутами.
        """
        return self.blueprint