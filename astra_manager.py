from flask import Flask, request, jsonify, render_template
import requests
import threading
import time
import logging
import json
import os

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
instances = []

def load_config(config_file):
    # Дефолтные настройки (вынесены для устранения дублирования)
    default_config = {
        "host": "127.0.0.1",
        "start_port": 9200,
        "end_port": 9300,
        "servers": [],
        "check_interval": 300,
        "flask_host": "127.0.0.1",
        "flask_port": 5000,
        "debug": False
    }
    
    # Если config_file пустой (передано пустое значение ASTRA_CONFIG), возвращаем дефолт
    if not config_file:
        logging.info("Переменная ASTRA_CONFIG пуста или не задана. Используем дефолтные настройки.")
        return default_config
    
    # Если файл не существует, создаём его с дефолтом и выходим
    if not os.path.exists(config_file):
        with open(config_file, 'w') as f:
            json.dump(default_config, f, indent=4)
        logging.info(f"Создан дефолтный конфиг-файл: {config_file}. Отредактируйте его и перезапустите приложение.")
        exit(0)
    
    # Пробуем загрузить из файла
    try:
        with open(config_file, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logging.error(f"Ошибка чтения конфиг-файла {config_file}: {e}. Используем дефолтные значения.")
        return default_config
    
def check_instance_alive(host, port):
    try:
        res = requests.get(f'http://{host}:{port}/api/instance', timeout=5)
        if res.ok:
            data = res.json()
            return data
    except Exception as e:
        logging.debug(f"Не удалось подключиться к {host}:{port}: {e}")
    return None

def perform_update(config):
    # Предполагаем, что check_instance_alive(host, port) возвращает dict с версией или None
    # Создаём словарь старых инстансов для быстрого доступа
    old_instances = {inst['addr']: inst for inst in instances}  # {addr: {'version': ..., 'status': ...}}
    
    temp_instances = {}  # Временный словарь для {addr: {'version': ..., 'status': ...}}
    host = config.get('host', 'localhost')
    start_port = config.get('start_port', 10000)
    end_port = config.get('end_port', 20000)
    servers = config.get('servers', [])  # Если нет - автосканирование
    
    if servers:
        # Проверка статических серверов
        for srv in servers:
            srv_host = srv['host']
            srv_port = srv['port']
            addr = f'{srv_host}:{srv_port}'
            instance_data = check_instance_alive(srv_host, srv_port)
            if instance_data:
                temp_instances[addr] = {
                    'version': instance_data.get('version', 'unknown'),
                    'status': 'Online'
                }
                logging.info(f"Сервер {addr}: онлайн, версия {temp_instances[addr]['version']}")
            elif addr in old_instances:
                # Для оффлайн: сохраняем старую версию из old_instances
                temp_instances[addr] = {
                    'version': old_instances[addr]['version'],  # Сохраняем предыдущую версию
                    'status': 'Offline'
                }
                logging.warning(f"Сервер {addr}: оффлайн (версия сохранена: {temp_instances[addr]['version']})")
            # Иначе (новый оффлайн) не добавляем
    else:
        # Автосканирование
        logging.info(f"Начинаем автосканирование {host}:{start_port}-{end_port}")
        for p in range(start_port, end_port + 1):
            addr = f'{host}:{p}'
            instance_data = check_instance_alive(host, p)
            if instance_data:
                temp_instances[addr] = {
                    'version': instance_data.get('version', 'unknown'),
                    'status': 'Online'
                }
                logging.info(f"Найден сервер {addr}, версия {temp_instances[addr]['version']}")
            elif addr in old_instances:
                # Для оффлайн: сохраняем старую версию из old_instances
                temp_instances[addr] = {
                    'version': old_instances[addr]['version'],  # Сохраняем предыдущую версию
                    'status': 'Offline'
                }
                logging.warning(f"Сервер {addr} остался оффлайн (версия сохранена: {temp_instances[addr]['version']})")
            # Новые оффлайн не добавляются
    
    # Обновляем instances атомарно
    new_instances = [{'addr': addr, **data} for addr, data in temp_instances.items()]
    instances[:] = new_instances
    logging.info(f"Обновлено {len(instances)} инстансов")

def update_instances(config):
    check_interval = config.get('check_interval', 300)
    while True:
        perform_update(config)
        time.sleep(check_interval)

# Serve the main UI
@app.route('/')
def index():
    return render_template('index.html')

# Get list of active instances (IPs + versions)
@app.route('/api/instances')
def get_instances():
    return jsonify(instances)

@app.route('/api/update_instances', methods=['POST'])
def api_update_instances():
    config_file = os.getenv('ASTRA_CONFIG', 'astra_config.json')
    config = load_config(config_file)
    threading.Thread(target=perform_update, args=(config,)).start()
    return jsonify({'message': 'Обновление запущено в фоне'})

# Proxy for getting channel list from selected Astra instance
@app.route('/api/get_channel_list', methods=['POST'])
def proxy_get_channel_list():
    addr = request.json['astra_addr']
    if not any(i['addr'] == addr for i in instances):
        return jsonify({})
    try:
        res = requests.post(f'http://{addr}/api/get_channel_list', json={}, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json() if res.ok and 'application/json' in res.headers.get('content-type', '') else {}
    except Exception as e:
        logging.error(f"Error proxying /api/get_channel_list to {addr}: {e}")
        return {}

# Proxy for kill stream/channel (stop/restart streaming)
@app.route('/api/control_kill_stream', methods=['POST'])
def proxy_control_kill_stream():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/control_kill_stream', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json() if res.ok else {'error': 'Failed to control stream'}
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/control_kill_channel', methods=['POST'])
def proxy_control_kill_channel():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/control_kill_channel', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json() if res.ok else {'error': 'Failed to control channel'}
    except Exception as e:
        return {'error': str(e)}

# Proxy for monitors
@app.route('/api/get_monitor_list', methods=['POST'])
def proxy_get_monitor_list():
    addr = request.json['astra_addr']
    try:
        res = requests.post(f'http://{addr}/api/get_monitor_list', json={}, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json() if res.ok else {}
    except Exception as e:
        return {}

@app.route('/api/control_kill_monitor', methods=['POST'])
def proxy_control_kill_monitor():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/control_kill_monitor', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json()
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/get_monitor_data', methods=['POST'])
def proxy_get_monitor_data():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/get_monitor_data', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json() if res.ok else {}
    except Exception as e:
        return {}

# For adapters (placeholder - no logic yet)
@app.route('/api/get_adapter_list', methods=['POST'])
def proxy_get_adapter_list():
    addr = request.json['astra_addr']
    # Placeholder: return empty or stub data
    return jsonify({'adapter_1': {'status': 'Online', 'signal': '80%'}})

# Proxy for killing/starting Astra instance
@app.route('/api/exit', methods=['POST'])
def proxy_exit():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/exit', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json()
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/reload', methods=['POST'])
def proxy_reload():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/reload', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json()
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/create_channel', methods=['POST'])
def proxy_create_channel():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/create_channel', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json()
    except Exception as e:
        return {'error': str(e)}

@app.route('/api/get_psi', methods=['POST'])
def proxy_get_psi():
    addr = request.json['astra_addr']
    data = {k: v for k, v in request.json.items() if k != 'astra_addr'}
    try:
        res = requests.post(f'http://{addr}/api/get_psi_channel', json=data, headers={'Content-Type': 'application/json'}, timeout=10)
        return res.json()
    except Exception as e:
        return {}

if __name__ == '__main__':
    # Загружаем конфиг
    config_file = os.getenv('ASTRA_CONFIG', 'astra_config.json')
    config = load_config(config_file)
    
    # Запускаем обновление в фоне
    threading.Thread(target=update_instances, args=(config,), daemon=True).start()
    
    # Запускаем Flask
    app.run(host=config['flask_host'], port=config['flask_port'], debug=config['debug'])
