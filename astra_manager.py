from flask import Flask, request, jsonify, render_template
import requests
import argparse
import threading
import time
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, template_folder='templates')
instances = []  # List of {'addr': 'ip:port', 'version': 'x.x.x'}

# Global variables for initial host and port
initial_host = None
initial_port = None

def update_instances():
    global instances, initial_host, initial_port
    
    if initial_host is None or initial_port is None:
        logging.error("Initial host and port not set.")
        return
    
    new_list = []
    
    # Scan the first 100 ports starting from the initial port
    for port in range(initial_port, initial_port + 100):
        addr = f'{initial_host}:{port}'
        if any(inst['addr'] == addr for inst in new_list):  # Avoid duplicates
            continue
        try:
            res = requests.get(f'http://{addr}/api/instance', timeout=5)
            if res.ok:
                new_inst = {'addr': addr, 'version': res.json().get('version', 'unknown')}
                new_list.append(new_inst)
                logging.info(f"Discovered new instance: {addr}")
        except Exception as e:
            pass 
    
    instances[:] = new_list
    # Schedule next update in 5 minutes
    threading.Timer(300, update_instances).start()

# Serve the main UI
@app.route('/')
def index():
    update_instances()
    return render_template('index.html')

# Get list of active instances (IPs + versions)
@app.route('/api/instances')
def get_instances():
    return jsonify(instances)

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
    parser = argparse.ArgumentParser(description='Astra Instance Management Tool')
    parser.add_argument('host', help='Initial Astra server IP address')
    parser.add_argument('port', type=int, help='Initial Astra server port')
    args = parser.parse_args()
    
    # Set global variables correctly
    initial_host = args.host
    initial_port = args.port
    
    # Start instance update loop
    update_instances()
    
    # Run the web server
    app.run(host='127.0.0.1', port=5000, debug=False)
