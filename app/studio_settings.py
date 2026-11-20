"""Host-owned engine and model locations. These are outside generation recipes."""
from copy import deepcopy
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

APP = Path(__file__).resolve().parent
ROOT = APP.parent
SETTINGS = ROOT / 'data/settings/studio.json'
COMPONENTS = {
    'h3': ('_CyTools/CyTools_MiniMaxH3', 'https://github.com/MrMybal/CyTools_MiniMaxH3'),
    'gaussian': ('_CyTools/CyTools_VideoToGaussian', 'https://github.com/MrMybal/CyTools_VideoToGaussian'),
    'stereo': ('_CyTools/CyTools_VideoToStereo3D', 'https://github.com/MrMybal/CyTools_VideoToStereo3D'),
    'cyvr': ('_VR/CyVR_VideoEnhancer', 'https://github.com/MrMybal/CyVR_VideoEnhancer'),
    'comfyflow': ('_CyTools/CyTools_ComfyFlow', 'https://github.com/MrMybal/CyTools_ComfyFlow'),
}
MODEL_CATEGORIES = (
    'checkpoints', 'diffusion_models', 'text_encoders', 'clip_vision', 'vae',
    'loras', 'controlnet', 'upscale_models', 'embeddings', 'unet', 'clip', 'video_models',
)
DEFAULTS = {
    'components': {}, 'ffmpegBin': '', 'modelLocations': [],
    'comfy': {
        'mode': 'installed', 'url': 'http://127.0.0.1:8188', 'root': '', 'python': '',
        'device': 'auto', 'gpuIndex': 0, 'memory': 'auto', 'vramGiB': 6, 'ramGiB': 4,
        'customNodes': True,
    },
}


def absolute(text):
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError('Choose an absolute path.')
    return path.resolve()


def endpoint(url, *, loopback=False):
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('Use an HTTP(S) server address without embedded credentials or query.')
    if loopback and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('An installed local server must use a loopback address.')
    return url.rstrip('/')


def load():
    data = json.loads(SETTINGS.read_text('utf-8')) if SETTINGS.is_file() else {}
    defaults = deepcopy(DEFAULTS)
    return {**defaults, **data, 'comfy': {**defaults['comfy'], **data.get('comfy', {})}}


def save(data):
    comfy = data['comfy']
    if comfy['mode'] not in ('installed', 'network', 'managed'):
        raise ValueError('Unknown ComfyUI mode.')
    if comfy['mode'] != 'managed':
        endpoint(comfy['url'], loopback=comfy['mode'] == 'installed')
    if comfy['device'] not in ('auto', 'cpu', 'cuda') or comfy['memory'] not in ('auto', 'low', 'none'):
        raise ValueError('Unknown ComfyUI compute or memory option.')
    if comfy['gpuIndex'] < 0 or comfy['ramGiB'] < 1 or comfy['vramGiB'] < 1:
        raise ValueError('Choose positive memory budgets and a valid GPU index.')
    for value in [data['ffmpegBin'], comfy['root'], comfy['python'], *data['components'].values()]:
        if value:
            absolute(value)
    for location in data['modelLocations']:
        if location['category'] not in (*MODEL_CATEGORIES, 'library'):
            raise ValueError('Unknown model category.')
        if '\n' in location['path'] or '\r' in location['path']:
            raise ValueError('A model path must occupy one line.')
        absolute(location['path'])
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_name(f'{SETTINGS.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        temporary.replace(SETTINGS)
    finally:
        temporary.unlink(missing_ok=True)


def component_root(component, settings):
    path = settings['components'].get(component)
    if path:
        return absolute(path)
    subdir, _ = COMPONENTS[component]
    relative = ROOT / 'runtime/components' / component
    workspace = ROOT.parents[2] / subdir if len(ROOT.parents) > 2 else relative
    return next((candidate for candidate in (relative, workspace) if candidate.is_dir()), relative)


def component_command(component, settings):
    root = component_root(component, settings)
    environments = ('.venv', 'runtime/python') if component == 'cyvr' else ('runtime/python',)
    executable = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    python = next((root / env / executable for env in environments if (root / env / executable).is_file()), None)
    entry = root / ('rtxvsr/core/pipeline.py' if component == 'cyvr' else 'app/tool.py')
    return root, python if entry.is_file() else None


def binary(name, settings):
    executable = name + '.exe' if os.name == 'nt' else name
    configured = settings['ffmpegBin']
    candidates = [absolute(configured) / executable] if configured else [
        ROOT / 'runtime/bin' / executable, component_root('cyvr', settings) / 'bin' / executable,
    ]
    found = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
    return found or (shutil.which(executable) if not configured else None)


def managed_python(config):
    if config['python']:
        return absolute(config['python'])
    root = absolute(config['root']) if config['root'] else ROOT / 'runtime/ComfyUI'
    parents = (root / '.venv', ROOT / 'runtime/comfy-python', root / 'venv')
    executable = 'Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'
    return next((parent / executable for parent in parents if (parent / executable).is_file()), None)


def model_status(settings):
    return [{**row, 'available': absolute(row['path']).exists()} for row in settings['modelLocations']]
