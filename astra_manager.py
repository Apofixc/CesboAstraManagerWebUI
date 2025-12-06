from flask import Flask, request, jsonify, render_template_string
import requests
import threading
import time
import sys
import json

app = Flask(__name__)
instances = set()  # Active Astra instances (IP:port)
initial_ip = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
initial_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000

def discover_instances():
    global instances
    try:
        # Assuming Astra has a way to list all known instances; for now, start from initial and add discovered ones
        response = requests.get(f'http://{initial_ip}:{initial_port}/api/instance', timeout=5)
        discovered = set(response.json().get('instances', []))
        instances.update(discovered)
    except:
        instances.add(f'{initial_ip}:{initial_port}')  # Default to initial
    instances = {inst for inst in instances if is_instance_alive(inst)}  # Filter alive ones
    threading.Timer(300, discover_instances).start()  # Refresh every 5 mins

def is_instance_alive(instance):
    try:
        requests.get(f'http://{instance}/api/ping', timeout=3)
        return True
    except:
        return False

discover_instances()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Astra Manager</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        :root {
            --primary-bg: linear-gradient(135deg, #0d1117 0%, #21262d 100%);
            --secondary-bg: #30363d;
            --card-bg: rgba(255, 255, 255, 0.08);
            --text-color: #f0f6fc;
            --accent-color: #58a6ff;
            --danger-color: #f85149;
            --success-color: #56d364;
            --border-color: rgba(255, 255, 255, 0.1);
            --shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            background: var(--primary-bg);
            color: var(--text-color);
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            height: 100vh;
            overflow-x: hidden;
        }

        .navbar {
            background: rgba(0, 0, 0, 0.9);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border-color);
            box-shadow: var(--shadow);
            position: sticky;
            top: 0;
            z-index: 1000;
        }

        .navbar-nav .nav-link { color: #c9d1d9; }
        .navbar-nav .nav-link:hover { color: var(--accent-color); }

        .tab-content .card { display: none; }
        .tab-content .active { display: block; }

        .card {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            box-shadow: var(--shadow);
            animation: fadeIn 0.4s ease-out;
        }

        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

        .table th { background: var(--secondary-bg); color: var(--accent-color); }
        .table td { border-color: var(--border-color); }

        .btn { border-radius: 8px; transition: all 0.2s; }
        .form-control { background: rgba(255, 255, 255, 0.05); color: var(--text-color); border: 1px solid var(--border-color); border-radius: 8px; }
        .modal-content { background: var(--secondary-bg); color: var(--text-color); }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="container-fluid">
            <span class="navbar-brand"><i class="bi bi-rocket"></i> Astra Manager</span>
            <div class="nav nav-tabs" role="tablist">
                <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#channels">Список каналов</button>
                <button class="nav-link" data-bs-toggle="tab" data-bs-target="#monitors">Мониторы</button>
                <button class="nav-link" data-bs-toggle="tab" data-bs-target="#adapters">Адаптеры</button>
                <button class="nav-link" data-bs-toggle="tab" data-bs-target="#create">Создать канал</button>
                <button class="nav-link" data-bs-toggle="tab" data-bs-target="#instances">Управление экземплярами</button>
            </div>
        </div>
    </nav>

    <div class="container mt-4 tab-content">
        <div id="channels" class="tab-pane active">
            <div class="card">
                <div class="card-header">
                    <h3>Список каналов</h3>
                </div>
                <div class="card-body">
                    <table class="table">
                        <thead><tr><th>Экземпляр</th><th>Канал</th><th>Действия</th></tr></thead>
                        <tbody id="channels-body"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="monitors" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <h3>Мониторы</h3>
                </div>
                <div class="card-body">
                    <table class="table">
                        <thead><tr><th>Экземпляр</th><th>Монитор</th><th>Bitrate</th><th>Статус</th><th>Действия</th></tr></thead>
                        <tbody id="monitors-body"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="adapters" class="tab-pane">
            <div class="card">
                <div class="card-body">
                    <p>Заглушка: Функционал адаптеров не реализован.</p>
                </div>
            </div>
        </div>

        <div id="create" class="tab-pane">
            <div class="card">
                <div class="card-body">
                    <form id="create-form">
                        <select id="create-instance" class="form-select mb-3"></select>
                        <textarea id="create-config" class="form-control mb-3" placeholder="JSON Config"></textarea>
                        <button type="submit" class="btn btn-primary">Создать канал</button>
                    </form>
                </div>
            </div>
        </div>

        <div id="instances" class="tab-pane">
            <div class="card">
                <div class="card-body">
                    <form id="manage-form">
                        <select id="manage-instance" class="form-select mb-3"></select>
                        <button type="button" onclick="manage('exit')" class="btn btn-danger">Exit</button>
                        <button type="button" onclick="manage('reload')" class="btn btn-warning">Reload</button>
                        <input id="interval" type="number" class="form-control mb-3" placeholder="Interval (optional)">
                    </form>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="psi-modal">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-body">
                    <pre id="psi-data"></pre>
                </div>
            </div>
        </div>
    </div>

    <script>
        const api = '/api';
        let channelsTimer, monitorsTimer;

        async function loadChannels() {
            const res = await fetch(`${api}/channels`);
            const data = await res.json();
            let html = '';
            Object.entries(data).forEach(([inst, chans]) => {
                Object.entries(chans).forEach(([id, name]) => {
                    html += `<tr>
                        <td>${inst}</td>
                        <td>${name}</td>
                        <td>
                            <button onclick="control('${inst}', '${name}', 'kill_stream', false)">Kill Stream</button>
                            <button onclick="control('${inst}', '${name}', 'kill_stream', true)">Reboot Stream</button>
                            <button onclick="control('${inst}', '${name}', 'kill_channel', false)">Kill Channel</button>
                            <button onclick="control('${inst}', '${name}', 'kill_channel', true)">Reboot Channel</button>
                        </td>
                    </tr>`;
                });
            });
            document.getElementById('channels-body').innerHTML = html || '<tr><td colspan="3">No channels</td></tr>';
        }

        async function loadMonitors() {
            const res = await fetch(`${api}/monitors`);
            const data = await res.json();
            let html = '';
            Object.entries(data).forEach(([inst, mons]) => {
                Object.entries(mons).forEach(([id, name]) => {
                    html += `<tr>
                        <td>${inst}</td>
                        <td><a href="#" onclick="showPSI('${inst}', '${name}')">${name}</a></td>
                        <td id="bitrate-${inst}-${name}">Loading...</td>
                        <td id="status-${inst}-${name}">Loading...</td>
                        <td>
                            <button onclick="control('${inst}', '${name}', 'kill_monitor', false)">Stop</button>
                            <button onclick="control('${inst}', '${name}', 'kill_monitor', true)">Restart</button>
                        </td>
                    </tr>`;
               });
            });
            document.getElementById('monitors-body').innerHTML = html || '<tr><td colspan="5">No monitors</td></tr>';
            await updateMonitorData();
        }

        async function updateMonitorData() {
            const res = await fetch(`${api}/monitor_data`);
            const data = await res.json();
            Object.entries(data).forEach(([inst, mons]) => {
                Object.entries(mons).forEach(([name, info]) => {
                    document.getElementById(`bitrate-${inst}-${name}`).textContent = info.bitrate;
                    document.getElementById(`status-${inst}-${name}`).textContent = info.status;
                });
            });
        }

        async function control(instance, channel, action, reboot) {
            const interval = document.getElementById('interval').value || null;
            await fetch(`${api}/control`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ instance, channel, action, reboot, interval })
            });
        }

        async function manage(action) {
            const instance = document.getElementById('manage-instance').value;
            const interval = document.getElementById('interval').value || null;
            await fetch(`${api}/manage_instance`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ instance, action, interval })
            });
        }

        async function showPSI(instance, channel) {
            const res = await fetch(`${api}/psi?instance=${instance}&channel=${channel}`);
            const psi = await res.json();
            document.getElementById('psi-data').textContent = JSON.stringify(psi, null, 2);
            new bootstrap.Modal(document.getElementById('psi-modal')).show();
        }

        document.getElementById('create-form').addEventListener('submit', async e => {
            e.preventDefault();
            const instance = e.target.elements[0].value;
            const config = e.target.elements[1].value;
            await fetch(`${api}/create_channel`, {
                method: 'POST',
                body: JSON.stringify({ instance, config }),
                headers: { 'Content-Type': 'application/json' }
            });
        });

        async function populateInstances() {
            const res = await fetch(`${api}/instances`);
            const data = await res.json();
            let html = '';
            data.instances.forEach(inst => html += `<option value="${inst}">${inst}</option>`);
            document.getElementById('create-instance').innerHTML = html;
            document.getElementById('manage-instance').innerHTML = html;
        }

        function startTimers() {
            loadChannels(); channelsTimer = setInterval(loadChannels, 60000);
            loadMonitors(); monitorsTimer = setInterval(updateMonitorData, 30000);
        }

        document.querySelectorAll('.nav-link').forEach(tab => {
            tab.addEventListener('shown.bs.tab', () => {
                clearInterval(channelsTimer); clearInterval(monitorsTimer);
                if (tab.getAttribute('data-bs-target') === '#channels') { loadChannels(); }
                if (tab.getAttribute('data-bs-target') === '#monitors') { startTimers(); }
            });
        });

        populateInstances(); startTimers();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/instances')
def get_instances():
    return jsonify({'instances': list(instances)})

@app.route('/api/channels')
def get_channels():
    data = {}
    for instance in instances:
        try:
            res = requests.get(f'http://{instance}/api/get_channel_list')
            data[instance] = res.json()
        except:
            data[instance] = {}
    return jsonify(data)

@app.route('/api/monitors')
def get_monitors():
    data = {}
    for instance in instances:
        try:
            res = requests.get(f'http://{instance}/api/get_monitor_list')
            data[instance] = res.json()
        except:
            data[instance] = {}
    return jsonify(data)

@app.route('/api/monitor_data')
def get_monitor_data():
    data = {}
    for instance in instances:
        try:
            res = requests.get(f'http://{instance}/api/get_monitor_data')
            monitor_data = res.json()
            for ch, info in monitor_data.items():
                if instance not in data: data[instance] = {}
                data[instance][ch] = {'bitrate': info.get('bitrate', 'N/A'), 'status': 'Онлайн' if info.get('online') else 'Оффлайн'}
        except:
            data[instance] = {}
    return jsonify(data)

@app.route('/api/psi')
def get_psi():
    instance = request.args.get('instance')
    channel = request.args.get('channel')
    try:
        res = requests.get(f'http://{instance}/api/get_monitor_data', params={'channel': channel})
        return jsonify(res.json())
    except:
        return jsonify({})

@app.route('/api/control', methods=['POST'])
def control():
    data = request.json
    instance = data['instance']
    channel = data['channel']
    action = data['action']
    reboot = data['reboot']
    interval = data.get('interval')
    params = {'channel': channel, 'reboot': reboot}
    if interval: params['interval'] = interval
    url_map = {'kill_stream': '/api/control_kill_stream', 'kill_channel': '/api/control_kill_channel', 'kill_monitor': '/api/control_kill_monitor'}
    try:
        requests.post(f'http://{instance}{url_map[action]}', json=params)
    except:
        pass
    return jsonify({'status': 'ok'})

@app.route('/api/manage_instance', methods=['POST'])
def manage_instance():
    data = request.json
    instance = data['instance']
    action = data['action']
    interval = data.get('interval')
    params = {}
    if interval: params['interval'] = interval
    url_map = {'exit': '/api/exit', 'reload': '/api/reload'}
    try:
        requests.post(f'http://{instance}{url_map[action]}', json=params)
    except:
        pass
    return jsonify({'status': 'ok'})

@app.route('/api/create_channel', methods=['POST'])
def create_channel():
    data = request.json
    instance = data['instance']
    config = data['config']
    try:
        requests.post(f'http://{instance}/api/create_channel', json={'config': json.loads(config)})
    except:
        pass
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
