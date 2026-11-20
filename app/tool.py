"""CyTools VideoStudio business runtime. UI and model frameworks stay in their own processes."""
import json
import os
import sys
from pathlib import Path

from cytools_core import CyToolError, Runtime, load_manifest
from cytools_core.communication import register_operations as register_communication
from cytools_core.shared_installation import register_operations as register_installation

import backend_adapter
import comfy_engine
import media_engine
from studio_manifest import GIB, MIB
from studio_settings import APP, COMPONENTS, ROOT, binary, component_command, load, managed_python, model_status

PERMISSIONS = ('network', 'filesystemExternal', 'manageResources')


class StudioRuntime(Runtime):
    def close(self, timeout=10):
        # Closing the app or MCP must not drop a still-running companion job.
        while True:
            try:
                return super().close(timeout=timeout)
            except CyToolError as error:
                if error.code != 'Busy':
                    raise


def diagnostics(context, settings, parameters):
    return {
        'components': {
            key: {'root': str(root), 'linked': python is not None, 'source': COMPONENTS[key][1]}
            for key in COMPONENTS for root, python in [component_command(key, settings)]
        },
        'ffmpeg': {key: binary(key, settings) for key in ('ffmpeg', 'ffprobe')},
        'comfy': {**settings['comfy'], 'managedPython': str(managed_python(settings['comfy']) or '')},
        'models': model_status(settings),
    }


NATIVE_HANDLERS = {
    'inspect_studio': diagnostics,
    'probe_video': lambda context, settings, p: media_engine.probe(context, settings, p['source'])[0],
    'preview_frame': media_engine.preview,
    'transcode_video': media_engine.transcode,
    'animate_still': media_engine.animate_still,
    'comfy_catalog': comfy_engine.catalog,
    'comfy_run': comfy_engine.run,
    'cyvr_enhance': lambda context, settings, p: backend_adapter.run(context, settings, 'cyvr', None, p),
}


def resolve_stereo_models(descriptor, settings):
    root, interpreter = component_command('stereo', settings)
    if not interpreter:
        return
    record = root / 'data/settings/model-storage.json'
    config = json.loads(record.read_text('utf-8')) if record.is_file() else {}
    base = Path(os.getenv('CYTOOLS_STEREO3D_MODEL_DIR') or config.get('models_dir') or root / 'data/models')
    for backend in descriptor['backends']:
        for model in backend.get('models', []):
            if model.get('location') != 'Local':
                continue
            folder = base / model['id']
            files = model.get('files', [])
            try:
                model['installed'] = bool(files) and (folder / 'installed.json').is_file() and all(
                    (folder / entry['path']).stat().st_size == entry['sizeBytes']
                    for entry in files if entry.get('required', True)
                )
            except OSError:
                model['installed'] = False


def handler_for(operation):
    meta = operation['extensions']['videoStudio']
    if 'component' not in meta:
        return lambda context, params: NATIVE_HANDLERS[operation['id']](context, load(), params)

    def invoke(context, params):
        # The Core attaches this selection to its own authenticated job.
        selection = context.runtime._jobs[context.job_id]
        return backend_adapter.run(context, load(), meta['component'], meta['operation'], params, preset=selection)
    return invoke


def estimate_for(operation, cpu_threads):
    identifier = operation['id']
    component = operation.get('extensions', {}).get('videoStudio', {}).get('component')

    def estimate(p, backend, model):
        settings = load()
        if component or identifier == 'cyvr_enhance':
            key = component or 'cyvr'
            if component_command(key, settings)[1] is None:
                raise CyToolError('MissingDependency', f'Link or install the {key} component in Components.', recoverable=True)
        if identifier in ('probe_video', 'preview_frame', 'transcode_video', 'animate_still'):
            for name in ('ffmpeg', 'ffprobe') if identifier != 'probe_video' else ('ffprobe',):
                if not binary(name, settings):
                    raise CyToolError('MissingDependency', f'Configure {name} in Components.')
        if identifier == 'transcode_video':
            pixels = p.get('width', 3840) * p.get('height', 2160)
            threads = min(p['threads'], cpu_threads)
            return {'cpuThreads': threads, 'ramBytes': max(192 * MIB, (10 + threads * 6) * pixels)}
        if identifier == 'cyvr_enhance' and (p['rtx_vsr'] or p['dlss_sr'] or p['dlss5']
                                                or p['format'] in ('hevc', 'hevc_mkv', 'h264')):
            return {'vramBytes': {'auto': p['vram_gib'] * GIB}}
        if identifier in ('comfy_run', 'comfy_catalog') and settings['comfy']['mode'] == 'managed':
            config = settings['comfy']
            interpreter = managed_python(config)
            if not interpreter or not interpreter.is_file() or not (comfy_engine.managed_root(config) / 'main.py').is_file():
                raise CyToolError('MissingDependency', 'Select a ComfyUI checkout and its private interpreter in Components.')
            estimate = {'ramBytes': config['ramGiB'] * GIB}
            if config['device'] == 'cuda':
                estimate['vramBytes'] = {str(config['gpuIndex']): config['vramGiB'] * GIB}
            return estimate
        if identifier == 'gaussian_reconstruct_scene':
            frames = (p.get('sequence_max_frames', 1500) if p.get('temporal_representation') == 'sequence'
                      else p.get('max_frames', 48))
            resolution = p.get('target_resolution', 640)
            return {'ramBytes': 2 * GIB + frames * resolution ** 2 * 24}
        return {}
    return estimate


def create_runtime(data_dir, **options):
    settings = load()
    descriptor = load_manifest(APP / 'CyTool.json')
    resolve_stereo_models(descriptor, settings)
    runtime = StudioRuntime(descriptor, data_dir, **options)
    runtime.configure_inputs(ROOT / 'data/inputs', max_bytes=32 * GIB, fields={
        op['id']: op['extensions']['videoStudio']['inputFiles']
        for op in descriptor['operations'] if op['extensions']['videoStudio']['inputFiles']
    })
    for operation in descriptor['operations']:
        runtime.register(operation['id'], handler_for(operation),
                         estimate=estimate_for(operation, runtime.system['cpuThreads']))
    register_communication(runtime)
    register_installation(runtime, ROOT)
    return runtime


def main():
    from cytools_core.transports._native import NativeHost

    arguments = sys.argv[1:]
    if '--data-dir' not in arguments:
        arguments = ['--data-dir', str(ROOT / 'data/jobs'), *arguments]
    try:
        with NativeHost(create_runtime, local_permissions=PERMISSIONS) as host:
            return host.call('run_cli', argv=arguments)
    except CyToolError as exc:
        print(json.dumps({'error': exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
