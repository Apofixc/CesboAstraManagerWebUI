from threading import Lock
from flask import Flask, request, jsonify, render_template, abort
import requests
import threading
import time
import logging
import json
import os
import requests.exceptions
import concurrent.futures
import signal
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, template_folder='templates')
instances = []
instances_lock = Lock()  # Блокировка для потокобезопасности обновлений
config = None

def load_config(config_file):
    """
    Загружает конфигурацию из файла JSON или использует дефолтные настройки.
    """
    default_config = {
        "host": "127.0.0.1",
        "start_port": 9200,
        "end_port": 9300,
        "servers": [],
        "check_interval": 300,
        "flask_host": "127.0.0.1",
        "flask_port": 5000,
        "debug": False,
        "scan_timeout": 5,
        "proxy_timeout": 15
    }
    
    if not config_file:
        logging.info("Переменная ASTRA_CONFIG пуста или не задана. Используем дефолтные настройки.")
        return default_config
    
    if not os.path.exists(config_file):
        try:
            with open(config_file, 'w') as f:
                json.dump(default_config, f, indent=4)
            logging.info(f"Создан дефолтный конфиг-файл: {config_file}. Отредактируйте его и перезапустите приложение.")
            sys.exit(0)
        except OSError as e:
            logging.error(f"Ошибка создания файла {config_file}: {e}")
            return default_config
    
    try:
        with open(config_file, 'r') as f:
            loaded = json.load(f)
            # Валидация базовых ключей
            for key in ['host', 'start_port', 'end_port', 'check_interval', 'flask_host', 'flask_port', 'debug', 'scan_timeout', 'proxy_timeout']:
                if key in loaded and not isinstance(loaded[key], (int, str, bool)):
                    logging.warning(f"Неверный тип для {key}, используем дефолт.")
                    loaded[key] = default_config[key]
            for srv in loaded.get('servers', []):
                if not isinstance(srv, dict) or 'host' not in srv or 'port' not in srv:
                    logging.error("Неверная структура servers, используем дефолт.")
                    loaded['servers'] = default_config['servers']
            return loaded
    except json.JSONDecodeError:
        logging.exception(f"Ошибка чтения конфиг-файла {config_file}. Используем дефолтные значения.")
        return default_config
    except OSError:
        logging.exception(f"Ошибка доступа к файлу {config_file}. Используем дефолтные значения.")
        return default_config

def check_instance_alive(host, port, scan_timeout):
    """
    Проверяет доступность одного экземпляра Astra.
    """
    try:
        res = requests.get(f'http://{host}:{port}/api/instance', timeout=scan_timeout)
        if res.ok:
            data = res.json()
            return data
    except requests.exceptions.RequestException as e:
        logging.debug(f"Не удалось подключиться к {host}:{port}: {e}")
    return None

def perform_update():
    """
    Обновляет список активных инстансов Astra (сканирование или статический список).
    Использует параллельное сканирование для скорости.
    """
    global config, instances
    old_instances = {inst['addr']: inst for inst in instances} 
    temp_instances = {} 
    
    host = config.get('host', '127.0.0.1')
    start_port = config.get('start_port', 9200)
    end_port = config.get('end_port', 9300)
    servers = config.get('servers', []) 
    scan_timeout = config.get('scan_timeout', 5) 

    target_addresses = []
    if servers:
        target_addresses = [(srv['host'], srv['port'], 'list') for srv in servers]
    else:
        target_addresses = [(host, p, 'autoscan') for p in range(start_port, end_port + 1)]

    # Параллельное сканирование для ускорения
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_addr = {executor.submit(check_instance_alive, srv_host, srv_port, scan_timeout): (srv_host, srv_port, srv_type) 
                          for srv_host, srv_port, srv_type in target_addresses}
        
        for future in concurrent.futures.as_completed(future_to_addr):
            srv_host, srv_port, srv_type = future_to_addr[future]
            addr = f'{srv_host}:{srv_port}'
            try:
                instance_data = future.result()
                if instance_data:
                    temp_instances[addr] = {
                        'version': instance_data.get('version', 'unknown'),
                        'status': 'Online'
                    }
                    logging.info(f"Сервер {addr}: онлайн, версия {temp_instances[addr]['version']}")
                else:
                    if addr in old_instances:
                        temp_instances[addr] = {
                            'version': old_instances[addr]['version'],
                            'status': 'Offline'
                        }
                    elif srv_type != 'autoscan':
                        temp_instances[addr] = {
                            'version': "unknown",
                            'status': 'Offline'
                        }
                    logging.debug(f"Сервер {addr}: оффлайн (версия сохранена)")
            except Exception as e:
                logging.error(f"Ошибка при проверке {addr}: {e}")

    # Обновляем instances атомарно
    with instances_lock:
        instances[:] = [{'addr': addr, **data} for addr, data in temp_instances.items()]
    logging.info(f"Обновлено {len(instances)} инстансов")

def update_instances():
    """
    Запускает бесконечный цикл обновления инстансов в фоновом потоке.
    """
    global config
    check_interval = config.get('check_interval', 300)
    while True:
        perform_update()
        time.sleep(check_interval)

def check_instance_online(addr):
    """Вспомогательная функция для проверки, онлайн ли инстанс."""
    with instances_lock: 
        return any(i['addr'] == addr and i['status'] == 'Online' for i in instances)

def proxy_request_helper(endpoint):
    """
    Универсальная функция для проксирования POST-запросов к Astra API с обработкой ошибок.
    Парсит addr и request_data из request.json сама, производит все проверки и проксирование.
    """
    proxy_timeout = config.get('proxy_timeout', 15)
    
    # Парсинг данных из запроса
    request_data = request.json
    addr = request_data.get('astra_addr') if request_data else None
    
    if not addr or not isinstance(addr, str):
        return jsonify({'error': 'Неверный или отсутствующий "astra_addr"'}), 400
    
    try:
        host, port = addr.split(':')
        int(port)
    except ValueError:
        return jsonify({'error': 'Неверный формат "astra_addr" (ожидается host:port)'}), 400

    # Проверка онлайн-статуса инстанса (централизованная проверка)
    if not check_instance_online(addr):
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
        return jsonify({'error': f'Непредвиденная ошибка: {str(e)}'}), 500

@app.route('/')
def index():
    """Serve the main UI."""
    return render_template('index.html')

@app.route('/api/instances')
def get_instances():
    """Get list of active instances (IPs + versions)."""
    # Добавлена блокировка для потокобезопасного чтения
    with instances_lock:
        return jsonify(instances)

@app.route('/api/update_instances', methods=['POST'])
def api_update_instances():
    """Trigger a manual background update of the instance list."""
    perform_update()
    with instances_lock:
        return jsonify(instances)

# Прокси-роуты: теперь просто вызывают helper с эндпоинтом
@app.route('/api/get_channel_list', methods=['POST'])
def proxy_get_channel_list():
    return proxy_request_helper('/api/get_channel_list')

@app.route('/api/control_kill_stream', methods=['POST'])
def proxy_control_kill_stream():
    return proxy_request_helper('/api/control_kill_stream')

@app.route('/api/control_kill_channel', methods=['POST'])
def proxy_control_kill_channel():
    return proxy_request_helper('/api/control_kill_channel')

@app.route('/api/get_monitor_list', methods=['POST'])
def proxy_get_monitor_list():
    return proxy_request_helper('/api/get_monitor_list')

@app.route('/api/control_kill_monitor', methods=['POST'])
def proxy_control_kill_monitor():
    return proxy_request_helper('/api/control_kill_monitor')

@app.route('/api/get_monitor_data', methods=['POST'])
def proxy_get_monitor_data():
    return proxy_request_helper('/api/get_monitor_data')

@app.route('/api/exit', methods=['POST'])
def proxy_exit():
    return proxy_request_helper('/api/exit')

@app.route('/api/reload', methods=['POST'])
def proxy_reload():
    return proxy_request_helper('/api/reload')

@app.route('/api/create_channel', methods=['POST'])
def proxy_create_channel():
    return proxy_request_helper('/api/create_channel')

@app.route('/api/get_psi', methods=['POST'])
def proxy_get_psi():
    return proxy_request_helper('/api/get_psi_channel')

@app.route('/api/get_adapter_list', methods=['POST'])
def proxy_get_adapter_list():
    return proxy_request_helper('/api/get_adapter_list')

def signal_handler(sig, frame):
    logging.info("Остановка приложения.")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    config = load_config(os.getenv('ASTRA_CONFIG', 'astra_config.json'))
    threading.Thread(target=update_instances, daemon=True).start()
    
    app.run(host=config['flask_host'], port=config['flask_port'], debug=config['debug'])
