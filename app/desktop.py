"""Dear ImGui / CyUI interface for the VideoStudio runtime."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import time

import cyui
from imgui_bundle import hello_imgui, imgui, immvision, portable_file_dialogs as pfd
from cytools_core import validate_parameters

from components_ui import ComponentsPanel
from studio_manifest import MEDIA_EXTENSIONS, TERMINAL
from studio_settings import ROOT, component_command, load
from tool import PERMISSIONS, create_runtime
import ui_common as ui

SECTIONS = {
    'Video': ('transcode_video', 'probe_video', 'preview_frame', 'h3_extract_frame', 'h3_concat_videos'),
    'Generation': ('h3_generate_video', 'h3_generate_film', 'h3_run_workflow', 'h3_generate_from_unreal_scene',
                   'h3_validate_request', 'h3_validate_film', 'h3_bridge_status', 'h3_list_workflows', 'h3_inspect_environment'),
    'Motion': ('animate_still', 'comfy_run'),
    'ComfyUI': ('comfy_run', 'comfy_catalog', 'comfyflow_import_workflow', 'comfyflow_inspect_workflow',
                'comfyflow_run_workflow', 'comfyflow_list_workflows', 'comfyflow_server_status'),
    'Stereo': ('stereo_convert_stereo3d', 'stereo_estimate_depth', 'stereo_package_stereo', 'stereo_analyze_video',
               'stereo_diagnose_backend', 'stereo_model_library_list', 'stereo_model_library_register'),
    'Enhance': ('cyvr_enhance', 'stereo_upscale_video'),
    'Gaussian': ('gaussian_reconstruct_scene', 'gaussian_generate_four_d_views', 'gaussian_generate_sharp_video',
                 'gaussian_generate_c4g_video', 'gaussian_analyze_video', 'gaussian_backend_status',
                 'gaussian_four_d_anyone_status', 'gaussian_sharp_status', 'gaussian_c4g_status'),
}
REFERENCE_FIELDS = {'checkpoint_path', 'depth_model_path', 'mhr70_regressor_path', 'path',
                    'reshade_preset', 'scene', 'film'}


def apply_style():
    theme = {'dark': 'navy', 'light': 'light-grey', 'classic': 'mid-grey'}[ui.preferences.values['theme']]
    cyui.set_locale(ui.preferences.values['language'])
    cyui.apply(theme, 'comfortable')
    imgui.get_style().font_scale_main = ui.preferences.values['font_scale']


def open_local(path):
    if os.name == 'nt':
        os.startfile(path)
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def first_video(obj, base):
    if isinstance(obj, dict):
        obj = obj.values()
    elif isinstance(obj, str):
        path = Path(obj)
        if path.suffix.lower() in MEDIA_EXTENSIONS and path.is_file() and path.resolve().is_relative_to(base):
            return str(path)
        return ''
    elif not isinstance(obj, list):
        return ''
    return next((match for value in obj if (match := first_video(value, base))), '')


class Studio:
    def __init__(self, runtime, actor, session):
        self.runtime, self.actor, self.session = runtime, actor, session
        self.operations = {entry['id']: entry for entry in runtime.descriptor['operations']}
        self.section, self.operation = 'Video', 'transcode_video'
        self.drafts = {}
        self.jobs = {}
        self.active = None
        self.error = ''
        self.components = ComponentsPanel()
        self.dialog = None
        self.selection = {'source': '', 'result': ''}
        self.image_cache = {}
        self.preview_dirty = set()
        self.preview_session = runtime.create_session(actor, temporary=True, retention_seconds=86400)['sessionId']
        self.preview_imports = {}
        self.preview_worker = ThreadPoolExecutor(max_workers=1)
        self.preview_pending = None
        self.frame_time = 0.0
        self.seek_jobs = {}
        self.last_finished = None
        self.server = None
        self.mcp_port = 8767
        self.mcp_profile = 0
        self.next_poll = 0

    def close(self):
        self.close_server()
        self.preview_worker.shutdown(wait=True, cancel_futures=True)
        self.runtime.finish_task(self.actor, self.preview_session, cancel_running=True)
        self.components.close()

    def submit_previews(self, selections, timestamp):
        submitted = {}
        self.runtime.keep_task_alive(self.actor, self.preview_session)
        for key, value in selections.items():
            if not value:
                continue
            source = Path(value).expanduser()
            if not source.is_absolute():
                source = ROOT / 'data/inputs' / source
            source = source.resolve()
            stat = source.stat()
            signature = (str(source), stat.st_mtime_ns, stat.st_size)
            staged = self.preview_imports.get(signature)
            if not staged or not Path(staged).is_file():
                staged = self.runtime.import_file(self.actor, self.preview_session, str(source))['path']
                self.preview_imports[signature] = staged
            job = self.runtime.submit(self.actor, self.preview_session, 'preview_frame',
                                      {'source': staged, 'time': timestamp})
            submitted[key] = job['jobId']
        return submitted

    def close_server(self):
        if self.server:
            self.server.server_close()
            self.server = None

    def mcp_panel(self):
        if not imgui.collapsing_header(ui.label('Local MCP and SDK')):
            return
        if self.server:
            imgui.input_text(ui.label('MCP address'), self.server.mcp_url, flags=imgui.InputTextFlags_.read_only)
            if cyui.button(ui.label('Copy MCP address')):
                imgui.set_clipboard_text(self.server.mcp_url)
            imgui.same_line()
            if cyui.button(ui.label('Stop local server')):
                self.close_server()
        else:
            profiles = ('free', 'local_controlled', 'token_only', 'protected_access')
            _, self.mcp_port = imgui.input_int(ui.label('Loopback port'), self.mcp_port)
            _, self.mcp_profile = imgui.combo(ui.label('Access profile'), self.mcp_profile,
                                              [ui.tr(profile) for profile in profiles])
            if cyui.button(ui.label('Start local MCP server'), 'primary'):
                from cytools_core.transports.http import make_server
                self.server = make_server(self.runtime, self.mcp_port, access_profile=profiles[self.mcp_profile],
                                          local_permissions=PERMISSIONS)

    def draft(self):
        if self.operation not in self.drafts:
            op = self.operations[self.operation]
            values = {}
            self.drafts[self.operation] = {'values': values, 'backend': '', 'model': ''}
        return self.drafts[self.operation]

    def payload(self):
        op = self.operations[self.operation]
        data = self.draft()
        properties = op['inputSchema'].get('properties', {})
        values = {}
        for key, value in data['values'].items():
            if value == '' and key not in op['inputSchema'].get('required', []):
                continue
            kind = properties.get(key, {}).get('type')
            if isinstance(value, str) and kind in ('number', 'integer', 'object', 'array'):
                value = {'number': float, 'integer': int, 'object': json.loads, 'array': json.loads}[kind](value)
            values[key] = value
        return validate_parameters(values, op['inputSchema'])

    def capture(self):
        data = self.draft()
        return {'operationId': self.operation, 'parameters': self.payload(), 'backendId': data['backend'],
                'modelId': data['model']}

    def restore(self, recipe):
        self.operation = recipe['operationId']
        self.section = next((key for key, ids in SECTIONS.items() if self.operation in ids), 'Advanced operations')
        self.drafts[self.operation] = {'values': recipe['parameters'], 'backend': recipe.get('backendId', ''),
                                       'model': recipe.get('modelId', '')}

    def draw_parameter(self, key, field, state, *, required, path):
        ui_label = ui.label(field.get('title', key.replace('_', ' ').capitalize()) + '###' + key)
        if required:
            ui_label = ui_label.replace('###', ' *###', 1)
        current = state.get(key, field.get('default'))
        if field.get('enum'):
            choices = field['enum']
            optional = not required and 'default' not in field
            options = ([None] if optional else []) + choices
            first = options.index(current) if current in options else 0
            changed, index = imgui.combo(ui_label, first, [ui.tr('Default') if x is None else str(x) for x in options])
            if changed or (required and key not in state):
                if options[index] is None:
                    state.pop(key, None)
                else:
                    state[key] = options[index]
        elif field.get('type') == 'boolean':
            changed, value = imgui.checkbox(ui_label, bool(current))
            if changed:
                state[key] = value
        else:
            value = '' if current is None else current if isinstance(current, str) else json.dumps(current, ensure_ascii=False)
            if (field.get('type') in ('object', 'array') or field.get('x-cy-kind') == 'MultilineString'
                    or (key == 'workflow' and field.get('type') != 'string')):
                changed, value = imgui.input_text_multiline(ui_label, value, (-200, 72))
            else:
                changed, value = imgui.input_text(ui_label, value)
            if changed:
                state[key] = value
            if path:
                imgui.same_line()
                if cyui.button(ui.label('Browse##' + key)):
                    self.dialog = (pfd.open_file(ui.tr('Select media or workflow')), state, key)
        if field.get('description') and imgui.is_item_hovered():
            imgui.set_tooltip(ui.tr(field['description']))

    def draw_operation(self):
        ids = SECTIONS[self.section]
        if self.operation not in ids:
            self.operation = ids[0]
        _, index = imgui.combo(ui.label('Operation'), ids.index(self.operation), [ui.tr(self.operations[key]['name']) for key in ids])
        self.operation = ids[index]
        op = self.operations[self.operation]
        schema = op['inputSchema']
        draft = self.draft()
        paths = op['extensions']['videoStudio']['inputFiles']
        imgui.text_wrapped(ui.tr(op['description']))
        if op.get('supportedBackends'):
            options = op['supportedBackends']
            current = draft['backend']
            if current not in options:
                draft['backend'] = options[0]
            changed, index = imgui.combo(ui.label('Backend'), options.index(draft['backend']), options)
            if changed:
                draft.update(backend=options[index], model='')
            backend = next(row for row in self.runtime.descriptor['backends'] if row['id'] == draft['backend'])
            known_models = [model['id'] for model in backend.get('models', [])
                            if model.get('installed') or model.get('location') == 'Remote']
            if known_models:
                models = ['', *known_models]
                selected = models.index(draft['model']) if draft['model'] in models else 0
                changed, selection = imgui.combo(ui.label('Model'), selected, [ui.tr('Default'), *known_models])
                if changed:
                    draft['model'] = models[selection]
        visible, advanced = [], []
        for key, field in schema.get('properties', {}).items():
            target = advanced if field.get('x-cy-ui', {}).get('advanced') else visible
            target.append((key, field))
        for key, field in visible:
            self.draw_parameter(key, field, draft['values'], required=key in schema.get('required', []),
                                path=key in paths or key in REFERENCE_FIELDS)
        if advanced and imgui.tree_node(ui.label('Advanced settings')):
            for key, field in advanced:
                self.draw_parameter(key, field, draft['values'], required=key in schema.get('required', []),
                                    path=key in paths or key in REFERENCE_FIELDS)
            imgui.tree_pop()
        cyui.space(6)
        if cyui.button(ui.label('Queue job'), 'primary'):
            payload = self.capture()
            job = self.runtime.submit(self.actor, self.session, self.operation, payload['parameters'],
                                      backend_id=payload['backendId'], model_id=payload['modelId'])
            self.jobs[job['jobId']] = job
            self.active = job['jobId']
            source = next((payload['parameters'][key] for key in ('source', 'video_file', 'video')
                           if isinstance(payload['parameters'].get(key), str)), '')
            if source:
                self.selection['source'] = source
            self.error = ''

    def refresh(self):
        if self.dialog and self.dialog[0].ready(0):
            dialog, values, key = self.dialog
            file = dialog.result()
            if file:
                values[key] = file[0]
                if key in ('source', 'video', 'video_file'):
                    self.selection['source'] = file[0]
                    self.image_cache.pop('source', None)
            self.dialog = None
        if time.monotonic() < self.next_poll:
            return
        self.next_poll = time.monotonic() + 0.15
        if self.preview_pending is not None and self.preview_pending.done():
            pending, self.preview_pending = self.preview_pending, None
            self.seek_jobs.update(pending.result())
        for job_id, job in self.jobs.items():
            if job['state'] not in TERMINAL:
                self.jobs[job_id] = self.runtime.get_job(self.actor, job_id)
        for source, job_id in list(self.seek_jobs.items()):
            job = self.runtime.get_job(self.actor, job_id)
            if job['state'] in TERMINAL:
                self.seek_jobs.pop(source)
                outputs = job.get('outputs') or {}
                if outputs.get('image_file'):
                    from PIL import Image
                    import numpy as np
                    with Image.open(outputs['image_file']) as image:
                        self.image_cache[source] = np.array(image.convert('RGB'))
                    self.preview_dirty.add(source)
                elif job.get('error'):
                    self.error = job['error']['message']
        active = self.jobs.get(self.active)
        if active and active['state'] == 'Completed' and active['jobId'] != self.last_finished:
            output = active.get('outputs') or {}
            self.selection['result'] = first_video(output, Path(active['workspace']).resolve())
            if self.selection['result']:
                self.image_cache.pop('result', None)
            self.last_finished = active['jobId']

    def queue_panel(self):
        cyui.section_title(ui.tr('Job queue'))
        ids = list(reversed(self.jobs))
        if not ids:
            cyui.text_dim(ui.tr('Queue an operation to see its progress here.'))
            return
        index = ids.index(self.active)
        labels = [f"{ui.tr(self.operations[self.jobs[key]['operationId']]['name'])} · {ui.tr(self.jobs[key]['state'])}"
                  for key in ids]
        changed, selected = imgui.combo(ui.label('Job'), index, labels)
        if changed:
            self.active = ids[selected]
        job = self.jobs[self.active]
        percent = job.get('progress', {}).get('percent', 0)
        imgui.progress_bar(percent / 100, (-1, 0), f'{percent:.0f}%')
        cyui.text_dim(ui.tr(job.get('progress', {}).get('currentStep') or job['state']))
        if job['state'] not in TERMINAL and cyui.button(ui.label('Cancel job')):
            self.runtime.cancel(self.actor, job['jobId'])
        if job.get('error'):
            imgui.text_wrapped(job['error']['message'])
        outputs = job.get('outputs') or {}
        if outputs:
            if outputs.get('width') and outputs.get('height'):
                cyui.text_dim(f"{ui.tr('Actual video')}: {outputs['width']} × {outputs['height']}")
            if self.selection['result'] and cyui.button(ui.label('Open video')):
                open_local(self.selection['result'])
            scene = outputs.get('scene_file')
            if isinstance(scene, str) and scene.lower().endswith('.json') and Path(scene).is_file():
                root, python = component_command('gaussian', load())
                if python and (root / 'app/player.py').is_file() and cyui.button(ui.label('Open Gaussian Player')):
                    subprocess.Popen([str(python), str(root / 'app/player.py'), scene],
                                     cwd=root, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if cyui.button(ui.label('Open job folder')):
                open_local(job['workspace'])
            if imgui.tree_node(ui.label('Output details')):
                imgui.input_text_multiline('##outputs', json.dumps(outputs, indent=2, ensure_ascii=False), (-1, 140),
                                           flags=imgui.InputTextFlags_.read_only)
                imgui.tree_pop()

    def preview_panel(self):
        cyui.section_title(ui.tr('Frame comparison'))
        _, self.frame_time = imgui.input_float(ui.label('Time (seconds)'), self.frame_time, 0.1, 1, '%.3f')
        imgui.begin_disabled(bool(self.seek_jobs or self.preview_pending or not any(self.selection.values())))
        if cyui.button(ui.label('Decode frames')):
            self.preview_pending = self.preview_worker.submit(self.submit_previews, dict(self.selection),
                                                              max(0, self.frame_time))
        imgui.end_disabled()
        for key, title in (('source', 'Source frame'), ('result', 'Result frame')):
            imgui.push_id(key)
            cyui.text_dim(ui.tr(title))
            value = self.selection[key]
            if value:
                imgui.text_wrapped(Path(value).name)
            if key in self.image_cache:
                array = self.image_cache[key]
                width = min(imgui.get_content_region_avail().x, 680)
                height = min(280, width * array.shape[0] / array.shape[1])
                width = height * array.shape[1] / array.shape[0]
                immvision.image_display('##preview', array, image_display_size=(int(width), int(height)),
                                        refresh_image=key in self.preview_dirty)
                self.preview_dirty.discard(key)
            imgui.pop_id()

    def workspace(self):
        if self.section == 'Components':
            self.mcp_panel()
            status = self.runtime.status()
            self.components.allow_switch = not (status['runningJobs'] or status['queuedJobs'])
            self.components.draw()
            return
        if imgui.begin_table('##studio-workspace', 2, imgui.TableFlags_.resizable):
            imgui.table_setup_column('parameters', imgui.TableColumnFlags_.width_stretch, 1.15)
            imgui.table_setup_column('preview', imgui.TableColumnFlags_.width_stretch, 0.85)
            imgui.table_next_column()
            imgui.begin_child('##parameters', (0, 0))
            try:
                self.draw_operation()
            finally:
                imgui.end_child()
            imgui.table_next_column()
            imgui.begin_child('##inspect', (0, 0))
            try:
                self.queue_panel()
                cyui.space(14)
                self.preview_panel()
            finally:
                imgui.end_child()
            imgui.end_table()


def main():
    from installation_ui import configure
    configure()
    apply = apply_style
    ui.apply_style = apply
    immvision.use_rgb_color_order()
    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_title = 'CyTools VideoStudio'
    runner.app_window_params.window_geometry.size = (1280, 820)
    runner.callbacks.load_additional_fonts = cyui.load_fonts
    runner.fps_idling.fps_max = 60
    (ROOT / 'data/settings').mkdir(parents=True, exist_ok=True)
    runner.ini_filename = str(ROOT / 'data/settings/imgui.ini')
    frames = 0
    with create_runtime(ROOT / 'data/jobs') as runtime:
        actor = runtime.authenticate(runtime.create_client('VideoStudio desktop', permissions=PERMISSIONS)['token'])
        session = runtime.create_session(actor, temporary=False)['sessionId']
        studio = Studio(runtime, actor, session)
        smoke_section = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--smoke-section=')), '')
        if smoke_section in (*SECTIONS, 'Components'):
            studio.section = smoke_section
        smoke_language = next((arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--smoke-language=')), '')
        if smoke_language in ('en', 'fr'):
            ui.preferences.values['language'] = smoke_language
        ui.configure_generation(runtime, studio.capture, studio.restore)
        ui.journal.bind(runtime, actor, only=actor)
        from advanced_ui import Panel
        from mesh_viewer_ui import install as install_viewer
        advanced = Panel(runtime, actor)
        mesh = install_viewer(runtime, ROOT, actor)

        def gui():
            nonlocal frames
            frames += 1
            if frames == 1:
                apply()
            ui.journal.poll()
            mesh.poll()
            try:
                studio.refresh()
            except Exception as error:
                studio.error = str(error)
            title = 'CyTools VideoStudio'
            cyui.heading(title)
            if imgui.begin_tab_bar('##studio-navigation'):
                for section in (*SECTIONS, 'Components'):
                    force = section == smoke_section and frames <= 3
                    opened, _ = imgui.begin_tab_item(ui.label(section),
                                                      flags=imgui.TabItemFlags_.set_selected if force else 0)
                    if opened:
                        studio.section = section
                        try:
                            studio.workspace()
                        except Exception as error:
                            studio.error = str(error)
                        finally:
                            if studio.error:
                                cyui.text_dim(studio.error)
                            imgui.end_tab_item()
                mesh.tab()
                advanced.draw_tab()
                ui.journal.draw_tab()
                ui.options_tab()
                imgui.end_tab_bar()
            cyui.render_notifications()
            if studio.components.restart:
                runner.app_shall_exit = True
            if '--smoke-test' in sys.argv and frames >= 16:
                runner.app_shall_exit = True
            if mesh.test:
                mesh.check_test(runner)

        runner.callbacks.show_gui = gui
        ui.bind_runtime(gui, actor)
        try:
            hello_imgui.run(runner)
            if '--smoke-test' in sys.argv:
                from standalone_log import save_screenshot
                path = ROOT / 'data/reports' / ('desktop-smoke-' + smoke_section.lower() + '.png'
                                                if smoke_section else 'desktop-smoke.png')
                path.parent.mkdir(parents=True, exist_ok=True)
                save_screenshot(path, hello_imgui.final_app_window_screenshot())
        finally:
            studio.close()
            mesh.close()
            advanced.close()
            runtime.finish_task(actor, session, cancel_running=True)


if __name__ == '__main__':
    main()
