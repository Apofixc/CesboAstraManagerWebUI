import logging
from xml.etree.ElementTree import tostring
import requests
from flask import Blueprint, request, Response, jsonify # type: ignore
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class ProxyRouter:
    def __init__(self, config_manager, instance_manager):
        self.config_manager = config_manager
        self.instance_manager = instance_manager
        self.proxy_endpoints = {
            '/api/get_channel_list': 'proxy_get_channel_list',
            '/api/get_monitor_list': 'proxy_get_monitor_list',
            '/api/get_monitor_data': 'proxy_get_monitor_data',
            '/api/get_psi_channel': 'proxy_get_psi_channel',
            '/api/get_adapter_list': 'proxy_get_adapter_list',
            '/api/get_adapter_data': 'proxy_get_adapter_data',
            '/api/control_kill_stream': 'proxy_control_kill_stream',
            '/api/control_kill_channel': 'proxy_control_kill_channel',
            '/api/control_kill_monitor': 'proxy_control_kill_monitor',
            '/api/exit': 'proxy_exit',
            '/api/reload': 'proxy_reload',
            '/api/create_channel': 'proxy_create_channel',
        }
        # Создаем blueprint с именем 'proxy'
        self.blueprint = Blueprint('proxy', __name__)
        self.setup_routes()

    def setup_routes(self):
        """Регистрирует роуты в blueprint."""
        # Основной роут для проксирования, ограничен POST (согласно логике оригинальной функции)
        for endpoint, func_name in self.proxy_endpoints.items():
            self.blueprint.add_url_rule(endpoint, func_name, lambda e=endpoint: self.proxy_request(e), methods=['POST'])
        
    def proxy_request(self, path):
        """Основной обработчик прокси-запроса, который вызывает proxy_request_helper."""
        try:
            # Вызываем оригинальную помощную функцию с endpoint (path)
            return self.proxy_request_helper(path)
        except Exception as e:
            logger.error(f"Ошибка в proxy_request: {e}")
            return Response("Proxy error", status=500)

    def proxy_request_helper(self, endpoint):
        """
        Универсальная функция для проксирования POST-запросов к Astra API.
        """
        if not self.config_manager:
            return jsonify({'error': 'Конфигурация не инициализирована'}), 500
      
        config = self.config_manager.get_config()
        proxy_timeout = config.get('proxy_timeout', 15)

        # Парсинг данных из запроса
        request_data = request.get_json()
        if not request_data or not isinstance(request_data, dict):
            return jsonify({'error': 'Неверный или отсутствующий JSON'}), 400

        addr = request_data.get('astra_addr')
        if not addr or not isinstance(addr, str):
            return jsonify({'error': 'Неверный или отсутствующий "astra_addr"'}), 400

        try:
            host, port = addr.split(':')
            int(port)
        except ValueError:
            return jsonify({'error': 'Неверный формат "astra_addr" (ожидается host:port)'}), 400

        # Проверка онлайн-статуса инстанса через InstanceManager
        if not self.instance_manager.check_instance_online(addr):
            return jsonify({'error': f'Инстанс {addr} не найден или оффлайн'}), 404

        url = f'http://{addr}{endpoint}'
        headers = {'Content-Type': 'application/json'}

        payload = {k: v for k, v in request_data.items() if k != 'astra_addr'}

        try:
            res = requests.post(url, json=payload, headers=headers, timeout=proxy_timeout)
            content_type = res.headers.get('content-type', '')

            if res.ok and 'application/json' in content_type:
                return jsonify(res.json()), res.status_code
            elif res.ok:
                return jsonify({'ok': 'Операция выполнена успешно'}), res.status_code
            else:
                return jsonify({'error': f'Ошибка на удаленном сервере: Статус {res.status_code}'}), 502

        except requests.exceptions.Timeout:
            logging.error(f"Таймаут подключения к {addr} на {endpoint}")
            return jsonify({'error': 'Превышен таймаут подключения к Astra'}), 504
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Ошибка подключения к {addr} на {endpoint}: {e}")
            return jsonify({'error': 'Ошибка подключения к Astra'}), 503
        except Exception as e:
            logging.exception(f"Непредвиденная ошибка при проксировании к {addr} на {endpoint}")
            return jsonify({'error': 'Непредвиденная ошибка'}), 500

    def get_blueprint(self):
        """Возвращает blueprint для регистрации в app.py."""
        return self.blueprint