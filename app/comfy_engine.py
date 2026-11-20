"""Client for trusted ComfyUI installations and private per-job local servers."""
import contextlib
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from cytools_core import CyToolError

from process_tracking import track
from studio_settings import MODEL_CATEGORIES, ROOT, absolute, endpoint, managed_python

MAX_JSON = 24 * 1024 ** 2
MAX_OUTPUT = 32 * 1024 ** 3


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request,fp,code,msg,headers,newurl):
        return None


class ComfyClient:
    def __init__(self, url, *, token=''):
        self.url = url
        self.headers = {'Authorization': 'Bearer ' + token} if token else {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def response(self, route, *, content=None, headers=None, timeout=15):
        request = urllib.request.Request(self.url + route, data=content, headers={**self.headers, **(headers or {})})
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                message = exc.read(3500).decode('utf-8', 'replace')
            raise CyToolError('ComfyHttpError', f'ComfyUI HTTP {exc.code}: {message}', details={'status': exc.code}) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise CyToolError('ComfyUnavailable', f'ComfyUI connection failed: {exc}', recoverable=True) from exc

    def call(self, route, payload=None):
        content = json.dumps(payload, allow_nan=False).encode('utf-8') if payload is not None else None
        with self.response(route, content=content, headers={'Content-Type': 'application/json'}) as reply:
            result = reply.read(MAX_JSON + 1)
        if len(result) > MAX_JSON:
            raise CyToolError('InputTooLarge', 'ComfyUI JSON response exceeds 24 MiB.')
        if not result.strip():
            return {}
        try:
            return json.loads(result)
        except ValueError as exc:
            raise CyToolError('ComfyProtocolError', 'ComfyUI returned invalid JSON.') from exc

    def upload(self, context, path, subfolder):
        identifier = uuid.uuid4().hex
        filename = identifier + path.suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,12}', path.suffix.lower()):
            raise CyToolError('InvalidInput', 'Choose an input file with a standard extension.')
        prefix = (
            f'--{identifier}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
            f'--{identifier}\r\nContent-Disposition: form-data; name="subfolder"\r\n\r\n{subfolder}\r\n'
            f'--{identifier}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('ascii')
        suffix = f'\r\n--{identifier}--\r\n'.encode('ascii')

        def chunks():
            yield prefix
            with path.open('rb') as handle:
                while chunk := handle.read(1024 * 1024):
                    context.cancellation.check()
                    yield chunk
            yield suffix

        with self.response('/upload/image', content=chunks(), headers={
                'Content-Type': f'multipart/form-data; boundary={identifier}',
                'Content-Length': str(len(prefix) + path.stat().st_size + len(suffix)),
        }, timeout=60) as reply:
            record = json.loads(reply.read(16384))
        if record.get('name') != filename or record.get('subfolder') != subfolder:
            raise CyToolError('ComfyProtocolError', 'ComfyUI returned an unexpected upload location.')
        return f'{subfolder}/{filename}'

    def abort(self, context, prompt_id):
        next_log = 0
        while True:
            try:
                self.call('/queue', {'delete': [prompt_id]})
                queue = self.call('/queue')
                if 'queue_running' not in queue or 'queue_pending' not in queue:
                    raise CyToolError('ComfyProtocolError', 'Server does not expose its execution queue.')
                running = any(len(row) > 1 and row[1] == prompt_id for row in queue['queue_running'])
                queued = any(len(row) > 1 and row[1] == prompt_id for row in queue['queue_pending'])
                if not running and not queued:
                    return
                if running:
                    self.call('/interrupt', {'prompt_id': prompt_id})
            except (CyToolError, OSError) as error:
                if time.monotonic() >= next_log:
                    next_log = time.monotonic() + 30
                    context.log(f'Waiting for the ComfyUI server to confirm job stop: {error}', level='Warning')
            time.sleep(0.8)


def model_paths(settings):
    result = {}
    for index, record in enumerate(settings['modelLocations']):
        path = absolute(record['path'])
        if not path.exists():
            continue
        category = record['category']
        if category == 'library' and path.is_dir():
            result[f'video_studio_{index}'] = {'base_path': str(path), **{key: key for key in MODEL_CATEGORIES}}
        elif category != 'library':
            result[f'video_studio_{index}'] = {category: str(path.parent if path.is_file() else path)}
    return result


def managed_root(config):
    return absolute(config['root']) if config['root'] else ROOT / 'runtime/ComfyUI'


@contextlib.contextmanager
def server(context, settings):
    config = settings['comfy']
    mode = config['mode']
    if mode != 'managed':
        yield ComfyClient(endpoint(config['url'], loopback=mode == 'installed'),
                          token=os.getenv('CYVIDEOSTUDIO_COMFY_TOKEN', '') if mode == 'network' else '')
        return
    root, python = managed_root(config), managed_python(config)
    if not (root / 'main.py').is_file() or python is None or not python.is_file():
        raise CyToolError('MissingDependency', 'Select a ComfyUI checkout and its private Python in Components.')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    config_path = context.path('comfy/model-paths.yaml')
    config_path.write_text(json.dumps(model_paths(settings), ensure_ascii=False), 'utf-8')
    command = [str(python), '-B', str(root / 'main.py'), '--listen', '127.0.0.1', '--port', str(port),
               '--disable-auto-launch', '--extra-model-paths-config', str(config_path)]
    for option in ('input', 'output', 'temp', 'user'):
        folder = context.path(f'comfy/{option}/.keep').parent
        command += [f'--{option}-directory', str(folder)]
    if not config['customNodes']:
        command.append('--disable-all-custom-nodes')
    if config['device'] == 'cpu':
        command.append('--cpu')
    elif config['device'] == 'cuda':
        command += ['--cuda-device', str(config['gpuIndex'])]
    if config['device'] != 'cpu' and config['memory'] in ('low', 'none'):
        command.append('--lowvram' if config['memory'] == 'low' else '--novram')
    with context.path('comfy/engine.log').open('wb') as log:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            track(context, process)
            client = ComfyClient(f'http://127.0.0.1:{port}')
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and process.poll() is None:
                context.cancellation.check()
                try:
                    client.call('/system_stats')
                except CyToolError:
                    context.cancellation.sleep(0.5)
                else:
                    break
            else:
                raise CyToolError('ComfyUnavailable', 'Private ComfyUI did not start. See comfy/engine.log.')
            yield client
        finally:
            stop_process(process)


def stop_process(process):
    import psutil
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()
    psutil.wait_procs(children, timeout=5)


def catalog(context, settings, params):
    with server(context, settings) as client:
        node = params.get('node_class')
        route = '/object_info/' + urllib.parse.quote(node, safe='') if node else '/object_info'
        return {'nodes': client.call(route)}


def load_graph(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 ** 2:
        raise CyToolError('InputTooLarge', 'Workflow JSON exceeds 8 MiB.')
    try:
        graph = json.loads(path.read_text('utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise CyToolError('InvalidParameters', 'Workflow must be valid JSON.') from exc
    if isinstance(graph, dict) and isinstance(graph.get('prompt'), dict):
        graph = graph['prompt']
    if not isinstance(graph, dict) or not graph or not all(
            isinstance(node, dict) and isinstance(node.get('class_type'), str) and isinstance(node.get('inputs'), dict)
            for node in graph.values()):
        raise CyToolError('InvalidParameters', 'Export API Format in ComfyUI, or import the editor graph with ComfyFlow.')
    return graph


def apply_bindings(context, client, graph, entries):
    used = set()
    for binding in entries:
        node, name = binding['node'], binding['input']
        if (node, name) in used or node not in graph or name not in graph[node]['inputs']:
            raise CyToolError('InvalidParameters', f'Unknown or repeated workflow binding: {node}.{name}')
        if ('file' in binding) == ('value' in binding):
            raise CyToolError('InvalidParameters', f'{node}.{name} requires exactly one file or value.')
        used.add((node, name))
    for binding in entries:
        value = (client.upload(context, Path(binding['file']), 'video_studio_' + context.job_id)
                 if 'file' in binding else binding['value'])
        graph[binding['node']]['inputs'][binding['input']] = value


def collect(context, client, entry):
    total = 0
    artifacts = []
    for node, outputs in entry.get('outputs', {}).items():
        if not isinstance(outputs, dict):
            continue
        for items in outputs.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if len(artifacts) >= 2048:
                    raise CyToolError('InputTooLarge', 'Workflow produced more than 2048 output artifacts.')
                prefix = f'{len(artifacts):04d}'
                if isinstance(item, str):
                    target = context.path(f'outputs/{prefix}.txt')
                    target.write_text(item, 'utf-8')
                elif isinstance(item, dict) and isinstance(item.get('filename'), str) and item.get('type', 'output') in ('output', 'temp'):
                    name = item['filename']
                    suffix = Path(name).suffix.lower()
                    extension = suffix if re.fullmatch(r'\.[a-z0-9]{1,12}', suffix) else '.bin'
                    target = context.path(f'outputs/{prefix}{extension}')
                    route = '/view?' + urllib.parse.urlencode({
                        'filename': name, 'subfolder': item.get('subfolder', ''), 'type': item.get('type', 'output'),
                    })
                    with client.response(route, timeout=60) as response, target.open('wb') as handle:
                        while chunk := response.read(1024 * 1024):
                            context.cancellation.check()
                            total += len(chunk)
                            if total > MAX_OUTPUT:
                                raise CyToolError('InputTooLarge', 'Workflow artifacts exceed the 32 GiB download limit.')
                            handle.write(chunk)
                else:
                    continue
                artifacts.append({'node': node, 'file': str(target),
                                  'mime_type': mimetypes.guess_type(target.name)[0] or 'application/octet-stream'})
    return artifacts


def run(context, settings, params):
    graph = load_graph(params['workflow'])
    managed = settings['comfy']['mode'] == 'managed'
    with server(context, settings) as client:
        if not managed:
            client.call('/system_stats')
        apply_bindings(context, client, graph, params.get('bindings', []))
        context.cancellation.check()
        expected_id = str(uuid.uuid4())
        try:
            submitted = client.call('/prompt', {'prompt': graph, 'prompt_id': expected_id, 'client_id': str(uuid.uuid4())})
        except CyToolError as error:
            if not managed and error.code in ('ComfyUnavailable', 'ComfyProtocolError'):
                client.abort(context, expected_id)
            raise
        prompt_id = submitted.get('prompt_id')
        if not isinstance(prompt_id, str) or not prompt_id:
            if not managed:
                client.abort(context, expected_id)
            raise CyToolError('ComfyProtocolError', 'Server did not return a prompt identifier.')
        deadline = time.monotonic() + params['timeout_seconds']
        try:
            context.progress(12, currentStep='Waiting for ComfyUI')
            route = '/history/' + urllib.parse.quote(prompt_id, safe='')
            while time.monotonic() < deadline:
                context.cancellation.check()
                item = client.call(route).get(prompt_id)
                if item:
                    status = item.get('status', {})
                    if status.get('status_str') in ('error', 'interrupted'):
                        raise CyToolError('ComfyExecutionFailed', 'ComfyUI reported workflow failure.',
                                          details=status.get('messages', [])[-3:])
                    if status.get('completed') or item.get('outputs'):
                        break
                context.cancellation.sleep(0.8)
            else:
                raise CyToolError('Timeout', 'ComfyUI exceeded the workflow deadline.')
        except (CyToolError, OSError) as error:
            if not managed and getattr(error, 'code', '') != 'ComfyExecutionFailed':
                client.abort(context, prompt_id)
            raise
        context.progress(88, currentStep='Collecting workflow outputs')
        return {'prompt_id': prompt_id, 'artifacts': collect(context, client, item)}
