import logging
import httpx # type: ignore
from quart import Blueprint, request, Response, jsonify # type: ignore
from typing import Dict

from App.config_manager import ConfigManager
from App.instance_manager import InstanceManager

logger = logging.getLogger(__name__)


class ProxyRouter:
    """
    Класс маршрутизатора прокси-сервера для перенаправления запросов к Astra API инстансам.

    Отвечает за прием POST-запросов от клиента, определение целевого инстанса Astra
    на основе данных запроса и асинхронное проксирование запроса.
    """

    def __init__(self, config_manager: ConfigManager, instance_manager: InstanceManager, http_client: httpx.AsyncClient):
        """
        Инициализирует ProxyRouter.

        Args:
            config_manager: Экземпляр ConfigManager для доступа к настройкам приложения (например, таймаутам).
            instance_manager: Экземпляр InstanceManager для проверки статуса целевых инстансов Astra.
        """
        self.config_manager = config_manager
        self.instance_manager = instance_manager
        self.http_client = http_client 
        # Словарь эндпоинтов, которые будут проксироваться
        self.proxy_endpoints: Dict[str, str] = {
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
        """
        Регистрирует прокси-маршруты в blueprint.

        Все зарегистрированные маршруты используют метод POST и направляют запрос
        в основной обработчик `proxy_request`.
        """
        for endpoint, func_name in self.proxy_endpoints.items():
            # Создаем асинхронный обработчик-замыкание для передачи endpoint
            async def handler(e=endpoint):
                return await self.proxy_request(e)
            # Регистрируем обработчик с уникальным именем
            self.blueprint.add_url_rule(endpoint, func_name, handler, methods=['POST'])
        
    async def proxy_request(self, path: str) -> Response:
        """
        Основной асинхронный обработчик, вызывающий вспомогательную функцию проксирования.

        Перехватывает и логирует исключения верхнего уровня, возникающие в процессе проксирования.

        Args:
            path: Целевой эндпоинт Astra API.

        Returns:
            Quart Response объект с результатом проксирования или ошибкой 500.
        """
        try:
            return await self.proxy_request_helper(path)
        except Exception as e:
            logger.error(f"Критическая ошибка в proxy_request для пути {path}: {e}", exc_info=True)
            return Response("Proxy error", status=500)

    async def proxy_request_helper(self, endpoint: str) -> Response:
        """
        Универсальная функция для проксирования POST-запросов к Astra API.

        Извлекает целевой адрес Astra ('astra_addr') из тела запроса клиента,
        проверяет его статус онлайн через InstanceManager и перенаправляет запрос.

        Args:
            endpoint: Конкретный эндпоинт Astra API, к которому идет обращение (например, '/api/reload').

        Returns:
            JSON-ответ от сервера Astra, либо JSON-ответ с описанием ошибки.
        """
        if not self.config_manager:
            return jsonify({'error': 'Конфигурация не инициализирована'}), 500
      
        config = self.config_manager.get_config()
        proxy_timeout = config.proxy_timeout

        # Парсинг данных из запроса
        request_data = await request.get_json()
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

        # Проверка онлайн-статуса инстанса
        if not await self.instance_manager.check_instance_online(addr):
            return jsonify({'error': f'Инстанс {addr} не найден или оффлайн'}), 404

        url = f'http://{addr}{endpoint}'
        headers = {'Content-Type': 'application/json'}

        # Удаляем 'astra_addr' из полезной нагрузки перед отправкой на сервер Astra
        payload = {k: v for k, v in request_data.items() if k != 'astra_addr'}

        try:
            async with httpx.AsyncClient(timeout=proxy_timeout) as client:
                res = await self.http_client.post(url, json=payload, headers=headers, timeout=proxy_timeout)
                content_type = res.headers.get('content-type', '')

                if res.status_code == 200:
                    if 'application/json' in content_type:
                        return jsonify(res.json()), res.status_code
                    else:
                        # Сервер Astra вернул 200 OK, но не JSON (например, пустой ответ)
                        return jsonify({'ok': 'Операция выполнена успешно'}), res.status_code
                else:
                    logger.error(f"Ошибка на удаленном сервере: Статус {res.status_code}, Ответ: {res.text}")
                    return jsonify({'error': f'Ошибка на удаленном сервере: Статус {res.status_code}'}), 502

        except httpx.TimeoutException:
            logger.error(f"Таймаут подключения к {addr} на {endpoint}")
            return jsonify({'error': 'Превышен таймаут подключения к Astra'}), 504
        except httpx.ConnectError as e:
            logger.error(f"Ошибка подключения к {addr} на {endpoint}: {e}")
            return jsonify({'error': 'Ошибка подключения к Astra'}), 503
        except Exception as e:
            logger.exception(f"Непредвиденная ошибка при проксировании к {addr} на {endpoint}")
            return jsonify({'error': 'Непредвиденная ошибка'}), 500

    def get_blueprint(self) -> Blueprint:
        """
        Возвращает сконфигурированный объект Blueprint для регистрации в основном приложении Quart.

        Returns:
            Экземпляр Quart Blueprint с зарегистрированными маршрутами проксирования.
        """
        return self.blueprint