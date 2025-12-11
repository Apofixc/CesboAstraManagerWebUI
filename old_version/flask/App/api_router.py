import json
import logging
from flask import Blueprint, render_template, Response, jsonify # type: ignore

logger = logging.getLogger(__name__)

class ApiRouter:
    def __init__(self, instance_manager):
        self.instance_manager = instance_manager
        self.blueprint = Blueprint('api', __name__, template_folder='../templates')
        self.setup_routes()

    def setup_routes(self):
        """Регистрирует роуты в blueprint."""
        self.blueprint.add_url_rule('/', 'index', self.index)
        self.blueprint.add_url_rule('/api/instances', 'get_instances', self.get_instances)
        self.blueprint.add_url_rule('/api/update_instances', 'api_update_instances', self.api_update_instances, methods=['POST'])

    def index(self):
        """Сервер главный UI."""
        return render_template('index.html')

    def get_instances(self):
        """SSE для потоковой передачи обновлений инстансов."""
        def generate():
            last_sent = None
            while True:
                self.instance_manager.update_event.wait(timeout=300)
                data = self.instance_manager.get_instances()

                if last_sent != data:
                    logger.info("Отправка обновления инстансов через SSE")
                    yield f"data: {json.dumps(data)}\n\n"
                    last_sent = data

                if self.instance_manager.update_event.is_set():
                    self.instance_manager.update_event.clear()

        return Response(generate(), mimetype='text/event-stream')

    def api_update_instances(self):
        """Ручное обновление инстансов."""
        data = self.instance_manager.manual_update()
        return jsonify(data)
    
    def get_blueprint(self):
        """Возвращает blueprint для регистрации в app.py."""
        return self.blueprint