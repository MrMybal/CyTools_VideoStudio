"""Build the versioned public manifest from the native component operation contracts."""
import copy
import json

from studio_settings import APP, COMPONENTS, component_root, load

MIB = 1024 ** 2
GIB = 1024 ** 3
MEDIA_EXTENSIONS = ['.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.mts', '.mxf', '.wmv']
COMPONENT_OPS = {
    'h3': (
        'generate_video', 'run_workflow', 'generate_from_unreal_scene', 'generate_film',
        'validate_film', 'validate_request', 'extract_frame', 'concat_videos',
        'bridge_status', 'list_workflows', 'inspect_environment',
    ),
    'gaussian': (
        'reconstruct_scene', 'analyze_video', 'backend_status', 'generate_four_d_views',
        'four_d_anyone_status', 'generate_sharp_video', 'sharp_status', 'generate_c4g_video', 'c4g_status',
    ),
    'stereo': (
        'convert_stereo3d', 'analyze_video', 'estimate_depth', 'upscale_video', 'package_stereo',
        'diagnose_backend', 'model_library_list', 'model_library_register',
    ),
    'comfyflow': ('import_workflow', 'list_workflows', 'inspect_workflow', 'run_workflow', 'server_status'),
}
H3_FILES = [
    'first_frame', 'last_frame', 'guides.*.file', 'references.images.*',
    'references.videos.*.file', 'references.audios.*', 'control.video', 'control.mask', 'control.source_video',
]
PATHS = {
    'h3_generate_video': H3_FILES, 'h3_validate_request': H3_FILES,
    'h3_extract_frame': ['video'], 'h3_concat_videos': ['videos.*'],
    'stereo_convert_stereo3d': ['video_file'], 'stereo_analyze_video': ['video_file'],
    'stereo_estimate_depth': ['video_file'], 'stereo_upscale_video': ['video_file'],
    'stereo_package_stereo': ['left_video', 'right_video'],
    'gaussian_reconstruct_scene': ['video_file'], 'gaussian_analyze_video': ['video_file'],
    'gaussian_generate_four_d_views': ['video_file'], 'gaussian_generate_sharp_video': ['video_file'],
    'gaussian_generate_c4g_video': ['video_file'],
}
TERMINAL = frozenset(('Completed', 'Failed', 'Cancelled'))


def field(kind, default=None, **kwargs):
    return {'type': kind, **({'default': default} if default is not None else {}), **kwargs}


def choice(values, default=None):
    return field('string', default, enum=list(values))


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def filename(kind='Video'):
    return field('string', minLength=1, **{'x-cy-kind': kind})


def platforms(windows='Experimental', linux='Experimental', macos='Unknown'):
    return [
        {'os': 'Windows', 'architecture': 'x64', 'status': windows},
        {'os': 'Linux', 'architecture': 'x64', 'status': linux},
        {'os': 'macOS', 'architecture': 'ARM64', 'status': macos},
    ]


def operation(identifier, name, description, inputs, required, outputs, *, paths=(), group=(),
              backend=(), permissions=(), ram=192 * MIB, disk=32 * MIB, win_only=False):
    return {
        'id': identifier, 'name': name, 'description': description,
        'inputSchema': obj(inputs, required),
        'outputSchema': obj(outputs, outputs),
        'outputs': [
            {'id': key, 'type': value.get('x-cy-kind', 'StructuredData'), 'description': key.replace('_', ' ')}
            for key, value in outputs.items()
        ],
        'resourceRequirements': {'ramBytes': ram, 'diskBytes': disk},
        'supportedBackends': list(backend), 'permissions': list(permissions),
        'platforms': platforms(linux='Unknown' if win_only else 'Experimental'),
        'canRunAsync': True, 'canCancel': True, 'supportsProgress': True,
        'extensions': {'videoStudio': {'inputFiles': list(paths)}},
        **({'exclusiveGroups': list(group)} if group else {}),
    }


WH = field('integer', minimum=2, maximum=8192, multipleOf=2)
SECOND = field('number', minimum=0)
RANGE = {'start': field('number', 0, minimum=0), 'end': SECOND}
SOURCE = {'source': filename()}
GEOMETRY = {
    'width': WH, 'height': WH, 'fit': choice(('contain', 'stretch', 'cover'), 'contain'),
    'filter': choice(('bilinear', 'bicubic', 'lanczos'), 'lanczos'),
    'layout': choice(('mono', 'sbs', 'hsbs', 'tb', 'htb'), 'mono'),
    'swap_eyes': field('boolean', False),
}
ENCODE = {
    'codec': choice(('h264', 'hevc', 'prores'), 'h264'),
    'quality': field('integer', 20, minimum=0, maximum=51),
    'speed': choice(('ultrafast', 'veryfast', 'fast', 'medium', 'slow'), 'veryfast'),
    'audio': choice(('encode', 'drop'), 'encode'),
    'threads': field('integer', 2, minimum=1, maximum=16),
}
VIDEO_OUT = {'video_file': filename(), 'width': field('integer'), 'height': field('integer'),
             'duration': field('number'), 'codec': field('string')}
COMFY_OUT = {'prompt_id': field('string'), 'artifacts': field('array', items=obj(
    {'node': field('string'), 'file': filename('File'), 'mime_type': field('string')},
    ('node', 'file', 'mime_type')))}
STAGES = ['neural_model', 'dlss_super_resolution', 'vfx_vsr', 'scale_method', 'reshade']
CYVR_INPUTS = {
    **SOURCE, **RANGE, 'width': WH, 'height': WH,
    'size_mode': choice(('preset', 'scale', 'exact', 'fit'), 'scale'),
    'preset': field('string', '1080p'), 'scale': field('number', 2, minimum=0.25, maximum=8),
    'layout': GEOMETRY['layout'], 'swap_eyes': GEOMETRY['swap_eyes'],
    'projection': choice(('flat', '180', '360', 'fisheye180'), 'flat'),
    'format': choice(('h264', 'hevc', 'hevc_mkv', 'prores_lt', 'prores_422', 'prores_hq', 'dnxhr_hq'), 'prores_422'),
    'encoder_preset': choice((f'p{i}' for i in range(1, 8)), 'p5'),
    'cq': field('integer', 21, minimum=0, maximum=51),
    'bframes': field('integer', minimum=0, maximum=4),
    'segment_frames': field('integer', 2000, minimum=1, maximum=20000),
    'hdr_policy': choice(('refuse', 'tonemap'), 'refuse'),
    'copy_audio': field('boolean', True), 'copy_subtitles': field('boolean', True),
    'rtx_vsr': field('boolean', True), 'dlss_sr': field('boolean', False), 'dlss5': field('boolean', False),
    'quality': choice(('BICUBIC', 'LOW', 'MEDIUM', 'HIGH', 'ULTRA', *(
        prefix + '_' + level for prefix in ('DENOISE', 'DEBLUR', 'HIGHBITRATE')
        for level in ('LOW', 'MEDIUM', 'HIGH', 'ULTRA'))), 'HIGH'),
    'dlss_sr_mode': choice(('DLAA', 'Quality', 'Balanced', 'Performance', 'Ultra Performance'), 'Quality'),
    'dlss_style': field('integer', 0, minimum=0, maximum=2),
    'dlss_strength': field('number', 1, minimum=0, maximum=2),
    'nr_flow_quality': choice(('High', 'Medium', 'Low'), 'High'),
    'sr_flow_quality': choice(('High', 'Medium', 'Low'), 'High'),
    'chain_order': field('array', STAGES, items=choice(STAGES), uniqueItems=True, minItems=5, maxItems=5),
    'denoise': field('integer', 0, minimum=0, maximum=100),
    'sharpen': field('integer', 0, minimum=0, maximum=100),
    'content_preset': field('string', 'Balanced'),
    'scale_filter': choice(('off', 'bilinear', 'bicubic', 'lanczos'), 'lanczos'),
    'reshade': field('boolean', False), 'reshade_mix': field('number', 1, minimum=0, maximum=1),
    'reshade_preset': filename('File'), 'reshade_shaders': filename('Directory'), 'reshade_textures': filename('Directory'),
    'gpu_backend': choice(('auto', 'cupy', 'torch'), 'auto'),
    'vram_gib': field('integer', 4, minimum=1, maximum=64),
}


def native_operations():
    return [
        operation('inspect_studio', 'Studio diagnostics', 'Report configured engines, local model references and available media binaries.',
                  {}, (), {'components': field('object'), 'ffmpeg': field('object'), 'comfy': field('object'),
                           'models': field('array')}),
        operation('probe_video', 'Inspect video', 'Read video and stream metadata without decoding the entire file.',
                  SOURCE, SOURCE, {'width': field('integer'), 'height': field('integer'), 'duration': field('number'),
                                   'codec': field('string'), 'streams': field('array')}, paths=('source',)),
        operation('preview_frame', 'Preview frame', 'Decode a bounded-resolution preview at the requested timestamp.',
                  {**SOURCE, 'time': field('number', 0, minimum=0),
                   'max_width': field('integer', 960, minimum=64, maximum=1920)},
                  SOURCE, {'image_file': filename('Image')}, paths=('source',)),
        operation('transcode_video', 'Resize, trim and encode', 'Resize, trim or transcode mono or packed stereo video with bounded FFmpeg workers.',
                  {**SOURCE, **RANGE, **GEOMETRY, **ENCODE, 'fps': field('number', minimum=1, maximum=240)},
                  SOURCE, VIDEO_OUT, paths=('source',), group=('media-encode',), ram=512 * MIB, disk=256 * MIB),
        operation('animate_still', 'Animate a still image', 'Create a keyframed camera move with pan, zoom and easing.',
                  {'image': filename('Image'), 'width': field('integer', 1280, minimum=64, maximum=3840, multipleOf=2),
                   'height': field('integer', 720, minimum=64, maximum=2160, multipleOf=2),
                   'seconds': field('number', 4, minimum=0.25, maximum=120),
                   'fps': field('integer', 24, minimum=12, maximum=60),
                   'from_zoom': field('number', 1, minimum=1, maximum=4),
                   'to_zoom': field('number', 1.25, minimum=1, maximum=4),
                   **{key: field('number', 0.5, minimum=0, maximum=1)
                      for key in ('from_x', 'from_y', 'to_x', 'to_y')},
                   'easing': choice(('linear', 'smooth'), 'smooth')},
                  ('image',), VIDEO_OUT, paths=('image',), group=('media-encode',), ram=512 * MIB, disk=256 * MIB),
        operation('comfy_catalog', 'ComfyUI nodes and models', 'Query the configured ComfyUI server for node schemas and model choices.',
                  {'node_class': field('string', maxLength=128)}, (), {'nodes': field('object')},
                  permissions=('network',), group=('comfy-engine',)),
        operation('comfy_run', 'Run ComfyUI API workflow', 'Run a ComfyUI API workflow and collect the generated video, audio, images and text.',
                  {'workflow': filename('File'), 'bindings': field('array', maxItems=64, items=obj(
                      {'node': field('string', minLength=1), 'input': field('string', minLength=1), 'value': {},
                       'file': filename('File')}, ('node', 'input'))),
                   'timeout_seconds': field('integer', 3600, minimum=10, maximum=86400)},
                  ('workflow',), COMFY_OUT, paths=('workflow', 'bindings.*.file'), permissions=('network',),
                  group=('comfy-engine', 'video-models'), ram=384 * MIB, disk=GIB),
        operation('cyvr_enhance', 'RTX Video / DLSS (CyVR)', 'Run the CyVR video engine with independent, ordered effects and a VR-aware output.',
                  CYVR_INPUTS, SOURCE, VIDEO_OUT, paths=('source',), group=('video-models',),
                  ram=2 * GIB, disk=4 * GIB, win_only=True),
    ]


def build():
    settings = load()
    operations, backends = native_operations(), []
    for component, ids in COMPONENT_OPS.items():
        path = component_root(component, settings) / 'app/CyTool.json'
        source = json.loads(path.read_text(encoding='utf-8-sig'))
        by_id = {op['id']: op for op in source['operations']}
        selected = [copy.deepcopy(by_id[op]) for op in ids]
        used = {backend for op in selected for backend in op.get('supportedBackends', ())}
        for entry in source['backends']:
            if entry['id'] not in used:
                continue
            entry.pop('dependencies', None)
            for model in entry.get('models', []):
                model['installed'] = False
            backends.append(entry)
        for op in selected:
            original = op['id']
            identifier = f'{component}_{original}'
            op['id'] = identifier
            op['name'] = f"{dict(h3='MiniMax H3', gaussian='Gaussian', stereo='Stereo 3D', comfyflow='ComfyFlow')[component]} - {op['name']}"
            op['platforms'] = platforms(linux='Unknown')
            op['extensions'] = {'videoStudio': {'component': component, 'operation': original,
                                                'inputFiles': PATHS.get(identifier, [])}}
            op['supportsProgress'] = True
            op['exclusiveGroups'] = ['video-models'] if component != 'h3' else ['h3-bridge']
            op.setdefault('permissions', [])
            if component in ('h3', 'comfyflow'):
                op['permissions'] = sorted(set(op['permissions']) | {'network'})
            if identifier in ('stereo_model_library_register', 'comfyflow_import_workflow'):
                op['permissions'] = sorted(set(op['permissions']) | {'filesystemExternal'})
            operations.append(op)
    return {
        'schemaVersion': 1, 'protocolVersion': '1.0', 'id': 'cy.tool.videostudio',
        'name': 'CyTools_VideoStudio', 'displayName': 'CyTools VideoStudio', 'version': '0.1.1',
        'description': 'Desktop video studio for AI generation, ComfyUI workflows, motion design, RTX/DLSS upscaling, stereo 3D and Gaussian splats. Built on CyToolsCore with MCP, SDK and CLI access.',
        'vendor': 'Cyberalien', 'license': 'AGPL-3.0-only',
        'licenseUrl': 'https://spdx.org/licenses/AGPL-3.0-only.html',
        'repository': 'https://github.com/MrMybal/CyTools_VideoStudio',
        'categories': ['video', 'generation'],
        'capabilities': ['jobs', 'cancellation', 'task-files'], 'platforms': platforms(),
        'interfaces': ['Native', 'CLI', 'JSONL', 'HTTP', 'MCP', 'ImGui'],
        'concurrency': {'policy': 'MultiWorker', 'maxWorkers': 2},
        'operations': operations, 'backends': backends, 'standaloneExecutable': 'CyTools_VideoStudio.exe',
        'extensions': {'sourceComponents': {key: url for key, (_, url) in COMPONENTS.items()}},
    }


if __name__ == '__main__':
    from cytools_core import validate_manifest
    manifest = build()
    validate_manifest(manifest)
    (APP / 'CyTool.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
