"""The operator's model, engine and component settings."""
from concurrent.futures import ThreadPoolExecutor
import webbrowser

from cytools_core.runtime import CancellationToken
from imgui_bundle import imgui, portable_file_dialogs as pfd
import cyui

import model_setup
from studio_settings import COMPONENTS, MODEL_CATEGORIES, ROOT, component_command, load, save
from ui_common import label, tr


class ComponentsPanel:
    def __init__(self):
        self.settings = load()
        self.draft = {'path': '', 'folder': '', 'url': '', 'name': '', 'hash': '',
                      'category': 0, 'tag': 'v0.36.0', 'torch': 0, 'update_url': '', 'update_hash': ''}
        self.accepted_model = False
        self.accepted_engine = False
        self.message = ''
        self.dialog = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.cancel = None
        self.downloaded = (0, None)
        self.restart = False

    def close(self):
        if self.cancel:
            self.cancel.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def browse(self, state, field, text, *, folder=True):
        changed, state[field] = imgui.input_text(label(text), state.get(field, ''))
        imgui.same_line()
        if cyui.button(label('Browse') + '##' + field):
            picker = pfd.select_folder(tr(text)) if folder else pfd.open_file(tr(text))
            self.dialog = (picker, state, field, folder)
        return changed

    def choose(self, key, inputs, text, options):
        _, index = imgui.combo(label(text), options.index(inputs[key]), [tr(option) for option in options])
        inputs[key] = options[index]

    def add_model(self, result):
        locations = self.settings['modelLocations']
        if result not in locations:
            locations.append(result)
        save(self.settings)
        self.message = tr('Model location saved')

    def start(self, mode, fn, *args):
        self.cancel = CancellationToken()
        self.downloaded = (0, None)
        self.pending = (mode, self.executor.submit(fn, *args, self.cancel, self.progress))
        self.message = ''

    def progress(self, done, total):
        self.downloaded = (done, total)

    def controls(self):
        if self.dialog and self.dialog[0].ready(0):
            picker, state, key, folder = self.dialog
            selection = picker.result()
            if selection:
                state[key] = selection if folder else selection[0]
            self.dialog = None
        if self.pending:
            mode, future = self.pending
            if future.done():
                self.pending = None
                try:
                    result = future.result()
                    if mode == 'model':
                        self.add_model(result)
                    elif mode == 'comfy':
                        self.settings['comfy'].update(result)
                        save(self.settings)
                        self.message = tr('ComfyUI installation ready')
                    else:
                        self.message = f"{tr('Update staged')} · {result}"
                except Exception as error:
                    self.message = str(error)
            else:
                done, total = self.downloaded
                imgui.progress_bar(done / total if total else -1, (0, 0), f'{done / 1024 ** 2:.1f} MiB')
                imgui.same_line()
                if cyui.button(label('Cancel transfer')):
                    self.cancel.cancel()
        if self.message:
            imgui.text_wrapped(self.message)

    def draw(self):
        self.controls()
        try:
            self.draw_settings()
        except (ValueError, OSError, RuntimeError) as error:
            self.message = str(error)

    def draw_settings(self):
        if imgui.collapsing_header(label('ComfyUI connection'), flags=imgui.TreeNodeFlags_.default_open):
            config = self.settings['comfy']
            self.choose('mode', config, 'Mode', ['installed', 'network', 'managed'])
            if config['mode'] == 'managed':
                self.browse(config, 'root', 'ComfyUI checkout')
                self.browse(config, 'python', 'Private Python executable', folder=False)
                self.choose('device', config, 'Compute device', ['auto', 'cpu', 'cuda'])
                self.choose('memory', config, 'VRAM policy', ['auto', 'low', 'none'])
                _, config['customNodes'] = imgui.checkbox(label('Load installed custom nodes'), config['customNodes'])
                _, config['gpuIndex'] = imgui.input_int(label('GPU index'), config['gpuIndex'])
                _, config['ramGiB'] = imgui.input_int(label('RAM budget (GiB)'), config['ramGiB'])
                _, config['vramGiB'] = imgui.input_int(label('VRAM budget (GiB)'), config['vramGiB'])
            else:
                _, config['url'] = imgui.input_text(label('Server address'), config['url'])
            if cyui.button(label('Save connection'), 'primary'):
                save(self.settings)
                self.message = tr('Settings saved')
            imgui.same_line()
            if config['mode'] != 'managed' and cyui.button(label('Open ComfyUI editor')):
                from studio_settings import endpoint
                webbrowser.open(endpoint(config['url'], loopback=config['mode'] == 'installed'))
            if imgui.tree_node(label('Install private ComfyUI')):
                _, self.draft['tag'] = imgui.input_text(label('Release tag'), self.draft['tag'])
                torch = list(model_setup.TORCH_OPTIONS)
                _, self.draft['torch'] = imgui.combo(label('PyTorch build'), self.draft['torch'], torch)
                _, self.accepted_engine = imgui.checkbox(label('I accept the engine and dependency licenses'), self.accepted_engine)
                if cyui.button(label('Official ComfyUI releases')):
                    webbrowser.open(model_setup.COMFY_REPOSITORY + '/releases')
                imgui.same_line()
                imgui.begin_disabled(self.pending is not None or not self.accepted_engine)
                if cyui.button(label('Download and install ComfyUI'), 'primary'):
                    self.start('comfy', model_setup.install_comfy, self.draft['tag'],
                               torch[self.draft['torch']], self.accepted_engine)
                imgui.end_disabled()
                imgui.tree_pop()

        if imgui.collapsing_header(label('Model locations'), flags=imgui.TreeNodeFlags_.default_open):
            imgui.text_wrapped(tr('Managed ComfyUI searches these locations when it starts. External servers use their own model libraries.'))
            categories = ('library', *MODEL_CATEGORIES)
            _, self.draft['category'] = imgui.combo(label('Model category'), self.draft['category'], list(categories))
            category = categories[self.draft['category']]
            self.browse(self.draft, 'path', 'Existing model or folder', folder=category == 'library')
            imgui.same_line()
            if category != 'library' and cyui.button(label('Select folder') + '##model'):
                self.dialog = (pfd.select_folder(tr('Select folder')), self.draft, 'path', True)
            if cyui.button(label('Register location'), 'primary'):
                self.add_model(model_setup.model_location(self.draft['path'], category))
            for index, location in enumerate(self.settings['modelLocations']):
                imgui.push_id(index)
                if cyui.button(label('Unlink')):
                    self.settings['modelLocations'].pop(index)
                    save(self.settings)
                    imgui.pop_id()
                    break
                imgui.same_line()
                imgui.text_wrapped(f"{location['category']}  ·  {location['path']}")
                imgui.pop_id()
            if imgui.tree_node(label('Download model')):
                for key, title in [('url', 'Direct HTTPS URL'), ('name', 'Model filename'), ('hash', 'SHA-256 (optional)')]:
                    _, self.draft[key] = imgui.input_text(label(title), self.draft[key])
                self.browse(self.draft, 'folder', 'Download folder (optional)')
                _, self.accepted_model = imgui.checkbox(label('I accept the model license'), self.accepted_model)
                imgui.begin_disabled(self.pending is not None or not self.accepted_model or category == 'library')
                if cyui.button(label('Download and register'), 'primary'):
                    self.start('model', model_setup.download_model, *(self.draft[key] for key in ('url', 'name', 'folder')),
                               category, self.draft['hash'], self.accepted_model)
                imgui.end_disabled()
                imgui.tree_pop()
            if cyui.button(label('Wan video workflows')):
                webbrowser.open('https://docs.comfy.org/tutorials/video/wan/wan2_2')
            imgui.same_line()
            if cyui.button(label('LTX video workflows')):
                webbrowser.open('https://docs.comfy.org/tutorials/video/ltxv/ltxv')

        if imgui.collapsing_header(label('Video engines')):
            for key, (_, url) in COMPONENTS.items():
                imgui.push_id(key)
                root, interpreter = component_command(key, self.settings)
                if cyui.button(label('GitHub and installer')):
                    webbrowser.open(url)
                imgui.same_line()
                imgui.text(f"{key}    {tr('Linked') if interpreter else tr('Installation required')}")
                self.browse(self.settings['components'], key, 'Engine root')
                imgui.text_disabled(str(root))
                imgui.pop_id()
            self.browse(self.settings, 'ffmpegBin', 'FFmpeg binaries folder')
            if cyui.button(label('Official FFmpeg downloads')):
                webbrowser.open('https://ffmpeg.org/download.html')
            imgui.same_line()
            if cyui.button(label('Save engine locations'), 'primary'):
                save(self.settings)
                self.message = tr('Settings saved')
        if imgui.collapsing_header(label('Application updates')):
            from updates import schedule, stage
            for key, title in [('update_url', 'GitHub release ZIP URL'), ('update_hash', 'Published SHA-256')]:
                _, self.draft[key] = imgui.input_text(label(title), self.draft[key])
            imgui.begin_disabled(self.pending is not None)
            try:
                if cyui.button(label('Download and verify update')):
                    self.start('update', stage, self.draft['update_url'], self.draft['update_hash'])
                folder = ROOT / 'runtime/update'
                stopped = not self.pending and self.allow_switch
                imgui.begin_disabled(not stopped)
                try:
                    if (folder / 'pending/app').is_dir() and cyui.button(label('Apply update and close'), 'primary'):
                        schedule('apply')
                        self.restart = True
                    if (folder / 'previous').is_dir() and cyui.button(label('Restore previous version and close')):
                        schedule('rollback')
                        self.restart = True
                    if not stopped:
                        imgui.text_wrapped(tr('Wait for running jobs to finish before switching versions.'))
                finally:
                    imgui.end_disabled()
            finally:
                imgui.end_disabled()
