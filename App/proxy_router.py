"""
Модуль для маршрутизации прокси-запросов к инстансам Astra API.

Обеспечивает перенаправление клиентских запросов к соответствующим
инстансам Astra, управляя их доступностью и обработкой ответов.
"""
import logging
from typing import Any, Dict, Optional, Tuple

import httpx # type: ignore
from quart import Blueprint, request, Response, jsonify # type: ignore

from astra_manager.App.config_manager import ConfigManager
from astra_manager.App.instance_manager import InstanceManager

logger = logging.getLogger(__name__)


class ProxyRouter:
    """
    Класс маршрутизатора прокси-сервера для перенаправления запросов к Astra API инстансам.

    Отвечает за прием POST-запросов от клиента, определение целевого инстанса Astra
    на основе данных запроса и асинхронное проксирование запроса.
    """

    def __init__(self, config_manager: ConfigManager,
                 instance_manager: InstanceManager, http_client: httpx.AsyncClient):
        """
        Инициализирует ProxyRouter.

        Args:
            config_manager (ConfigManager): Экземпляр ConfigManager для доступа к настройкам 
                                            приложения (например, таймаутам).
            instance_manager (InstanceManager): Экземпляр InstanceManager для проверки статуса 
                                                целевых инстансов Astra.
            http_client (httpx.AsyncClient): Асинхронный HTTP-клиент для выполнения запросов.
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

    async def proxy_request(self, path: str) -> Tuple[Response, int]:
        """
        Основной асинхронный обработчик, вызывающий вспомогательную функцию проксирования.

        Перехватывает и логирует исключения верхнего уровня, возникающие в процессе проксирования.

        Args:
            path (str): Целевой эндпоинт Astra API.

        Returns:
            Tuple[Response, int]: Объект Quart Response с результатом проксирования или ошибкой 500.
        """
        try:
            return await self.proxy_request_helper(path)
        except Exception as err: # pylint: disable=W0718
            logger.error("Критическая ошибка в proxy_request для пути %s: %s", path, err, exc_info=True)
            return jsonify({'error': 'Непредвиденная ошибка проксирования',
                            'message': 'Произошла непредвиденная ошибка на сервере.'}), 500

    async def _validate_proxy_request_data(self, request_data: Any) -> \
            Tuple[Optional[str], Optional[Tuple[Dict[str, Any], int]]]:
        """
        Валидирует входные данные для прокси-запроса.

        Проверяет наличие и корректность `astra_addr` в теле запроса.

        Args:
            request_data (Any): Сырые данные запроса.

        Returns:
            Tuple[Optional[str], Optional[Tuple[Dict[str, Any], int]]]:
                Кортеж, содержащий:
                - `str` (адрес Astra) или `None`, если валидация не пройдена.
                - Кортеж `(JSON-ответ с ошибкой, HTTP-статус)` или `None`,
                  если валидация успешна.
        """
        error_response: Optional[Tuple[Dict[str, Any], int]] = None
        addr: Optional[str] = None

        if not request_data or not isinstance(request_data, dict):
            error_response = ({'error': 'Неверный или отсутствующий JSON'}, 400)
        else:
            addr = request_data.get('astra_addr')
            if not addr or not isinstance(addr, str):
                error_response = ({'error': 'Неверный или отсутствующий "astra_addr"'}, 400)
            else:
                try:
                    _, port_str = addr.split(':')
                    port = int(port_str)
                    if not 1 <= port <= 65535:
                        raise ValueError("Порт должен быть в диапазоне 1-65535")
                except ValueError:
                    error_response = ({'error': 'Неверный формат "astra_addr" (ожидается host:port с валидным портом)'}, 400)

                if not error_response and not await self.instance_manager.check_instance_online(addr):
                    error_response = ({'error': f'Инстанс {addr} не найден или оффлайн'}, 404)

        return addr, error_response

    async def _handle_proxy_http_request(self, addr: str, endpoint: str, payload: Dict[str, Any],
                                         proxy_timeout: int) -> Tuple[Dict[str, Any], int]:
        """
        Выполняет HTTP-запрос к целевому инстансу Astra и обрабатывает ответ.

        Args:
            addr (str): Адрес инстанса Astra.
            endpoint (str): Целевой эндпоинт Astra API.
            payload (Dict[str, Any]): Полезная нагрузка для отправки.
            proxy_timeout (int): Таймаут для HTTP-запроса.

        Returns:
            Tuple[Dict[str, Any], int]: Кортеж, содержащий JSON-ответ от сервера Astra
                                        и HTTP-статус.
        """
        url = f'http://{addr}{endpoint}'
        headers = {'Content-Type': 'application/json'}

        try:
            res = await self.http_client.post(url, json=payload, headers=headers,
                                               timeout=proxy_timeout)
            response_data: Dict[str, Any]
            status_code: int

            try:
                response_data, status_code = res.json(), res.status_code
            except ValueError:
                logger.warning("Неверный JSON-ответ от удаленного сервера со статусом %s",
                               res.status_code)
                response_data, status_code = {'error': f'Ошибка на удаленном сервере: Статус {res.status_code}',
                                              'details': res.text}, res.status_code

        except httpx.TimeoutException:
            logger.error("Таймаут подключения к %s на %s", addr, endpoint)
            response_data, status_code = {'error': 'Превышен таймаут подключения к Astra'}, 504
        except httpx.ConnectError as err:
            logger.error("Ошибка подключения к %s на %s: %s", addr, endpoint, err)
            response_data, status_code = {'error': 'Ошибка подключения к Astra'}, 503
        except Exception as err: # pylint: disable=W0718
            logger.error("Непредвиденная ошибка при проксировании к %s на %s: %s",
                             addr, endpoint, err, exc_info=True)
            response_data, status_code = {'error': 'Непредвиденная ошибка'}, 500

        return response_data, status_code

    async def proxy_request_helper(self, endpoint: str) -> Tuple[Response, int]:
        """
        Универсальная функция для проксирования POST-запросов к Astra API.

        Извлекает целевой адрес Astra ('astra_addr') из тела запроса клиента,
        проверяет его статус онлайн через InstanceManager и перенаправляет запрос.

        Args:
            endpoint (str): Конкретный эндпоинт Astra API, к которому идет обращение
                            (например, '/api/reload').

        Returns:
            Tuple[Response, int]: JSON-ответ от сервера Astra, либо JSON-ответ с описанием ошибки.
        """
        config = self.config_manager.get_config()
        proxy_timeout = config.proxy_timeout

        request_data = await request.get_json()
        addr, validation_error_tuple = await self._validate_proxy_request_data(request_data)
        if validation_error_tuple:
            response_data, status_code = validation_error_tuple
            return jsonify(response_data), status_code

        assert addr is not None, "addr должен быть строкой после валидации"

        # Удаляем 'astra_addr' из полезной нагрузки перед отправкой на сервер Astra
        payload = {k: v for k, v in request_data.items() if k != 'astra_addr'}

        response_data, status_code = await self._handle_proxy_http_request(
            addr, endpoint, payload, proxy_timeout
        )
        return jsonify(response_data), status_code

    def get_blueprint(self) -> Blueprint:
        """
        Возвращает сконфигурированный объект Blueprint.

        Этот Blueprint содержит все зарегистрированные маршруты проксирования
        и готов к регистрации в основном приложении Quart.

        Returns:
            Blueprint: Экземпляр Quart Blueprint с зарегистрированными маршрутами проксирования.
        """
        return self.blueprint
