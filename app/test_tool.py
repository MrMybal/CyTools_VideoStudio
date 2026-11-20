from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from studio_settings import binary, component_command, load
from tool import PERMISSIONS, create_runtime

SETTINGS = load()


@contextmanager
def stub_comfy(*, finish=True):
    status = {'prompt': '', 'uploads': [], 'queue': [], 'interrupted': [], 'submitted': threading.Event()}

    class Server(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, payload, code=200):
            content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.path.startswith('/history/'):
                if finish and status['prompt']:
                    self.send({status['prompt']: {'status': {'completed': True},
                              'outputs': {'3': {'gifs': [{'filename': 'output.mp4', 'type': 'output'}]}}}})
                else:
                    self.send({})
            elif self.path == '/queue':
                self.send({'queue_running': status['queue'], 'queue_pending': []})
            elif self.path.startswith('/view?'):
                self.send(b'expected artifact from fixture')
            else:
                self.send({'SaveVideo': {}})

        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/upload/image':
                import re
                name = re.search(rb'filename="([^"]+)"', data)[1].decode()
                folder = re.search(rb'name="subfolder"\r\n\r\n([^\r]+)', data)[1].decode()
                status['uploads'].append(data)
                self.send({'name': name, 'subfolder': folder, 'type': 'input'})
                return
            payload = json.loads(data)
            if self.path == '/prompt':
                status['prompt'] = payload['prompt_id']
                status['graph'] = payload['prompt']
                status['queue'] = [[0, status['prompt']], [1, 'unrelated-job']]
                self.send({'prompt_id': status['prompt']})
                status['submitted'].set()
            elif self.path == '/interrupt':
                status['interrupted'].append(payload['prompt_id'])
                status['queue'] = [job for job in status['queue'] if job[1] != payload['prompt_id']]
                self.send({})
            else:
                self.send({})

    with ThreadingHTTPServer(('127.0.0.1', 0), Server) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            config = {**SETTINGS, 'comfy': {**SETTINGS['comfy'], 'mode': 'network',
                                           'url': f'http://127.0.0.1:{server.server_port}'}}
            with patch('tool.load', return_value=config):
                yield status
        finally:
            server.shutdown()
            thread.join()


class VideoStudioTests(unittest.TestCase):
    def run_job(self, runtime, actor, session, operation, params, **kwargs):
        handle = runtime.submit(actor, session, operation, params, **kwargs)
        result = runtime.wait(actor, handle['jobId'], timeout=45)
        error = result['error']
        if error:
            log = Path(result['workspace']) / 'adapter/backend.log'
            self.fail(f"{error}\n{log.read_text(errors='replace')[-3000:] if log.exists() else ''}")
        return result['outputs']

    @contextmanager
    def runtime(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with create_runtime(root / 'jobs') as runtime:
                runtime.configure_inputs(root / 'inputs', fields=runtime.input_fields)
                actor = runtime.authenticate(runtime.create_client('integration', permissions=PERMISSIONS)['token'])
                session = runtime.create_session(actor)['sessionId']
                yield root, runtime, actor, session

    def test_comfy_streaming_bindings_and_task_cleanup(self):
        with stub_comfy() as status, self.runtime() as (root, runtime, actor, session):
            workflow = root / 'project.json'
            image = root / 'input.png'
            image.write_bytes(b'fixture image bytes')
            workflow.write_text(json.dumps({'1': {'class_type': 'LoadImage', 'inputs': {'image': 'placeholder'}},
                                            '2': {'class_type': 'Seed', 'inputs': {'seed': 0}}}))
            result = self.run_job(runtime, actor, session, 'comfy_run', {'workflow': str(workflow), 'bindings': [
                {'node': '1', 'input': 'image', 'file': str(image)}, {'node': '2', 'input': 'seed', 'value': 42}
            ]})
            out = Path(result['artifacts'][0]['file'])
            self.assertEqual(out.read_bytes(), b'expected artifact from fixture')
            self.assertIn(b'fixture image bytes', status['uploads'][0])
            self.assertEqual(status['graph']['2']['inputs']['seed'], 42)
            runtime.finish_task(actor, session)
            self.assertFalse(out.exists())
            self.assertTrue(image.exists())

    def test_comfy_cancel_targets_only_owned_prompt(self):
        with stub_comfy(finish=False) as status, self.runtime() as (root, runtime, actor, session):
            graph = root / 'project.json'
            graph.write_text('{"1":{"class_type":"SaveVideo","inputs":{}}}')
            job = runtime.submit(actor, session, 'comfy_run', {'workflow': str(graph)})
            self.assertTrue(status['submitted'].wait(10))
            runtime.cancel(actor, job['jobId'])
            end = runtime.wait(actor, job['jobId'], timeout=15)
            self.assertEqual(end['state'], 'Cancelled')
            self.assertEqual(status['interrupted'], [status['prompt']])
            self.assertEqual(status['queue'], [[1, 'unrelated-job']])

    def fixture(self, root):
        ffmpeg = binary('ffmpeg', SETTINGS)
        if not ffmpeg:
            self.skipTest('No linked FFmpeg')
        path = root / 'stereo.mp4'
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x96:rate=24',
                        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '1', '-threads', '1',
                        '-c:v', 'libx264', '-c:a', 'aac', str(path)], check=True, timeout=20)
        return str(path)

    def test_ffmpeg_preview_stereo_resize_and_motion(self):
        with self.runtime() as (root, runtime, actor, session):
            source = self.fixture(root)
            original = self.run_job(runtime, actor, session, 'probe_video', {'source': source})
            self.assertEqual((original['width'], original['height']), (160, 96))
            self.assertTrue(any(row['codec_type'] == 'audio' for row in original['streams']))
            image = self.run_job(runtime, actor, session, 'preview_frame', {'source': source, 'time': 0.4})
            output = self.run_job(runtime, actor, session, 'transcode_video', {'source': source,
                                  'layout': 'sbs', 'swap_eyes': True, 'width': 320, 'height': 192, 'end': 0.5})
            self.assertEqual((output['width'], output['height'], output['codec']), (320, 192, 'h264'))
            self.assertAlmostEqual(output['duration'], .5, places=1)
            motion = self.run_job(runtime, actor, session, 'animate_still', {'image': image['image_file'],
                                  'width': 256, 'height': 144, 'seconds': .5})
            self.assertEqual((motion['width'], motion['height'], motion['codec']), (256, 144, 'h264'))
            self.assertAlmostEqual(motion['duration'], .5, places=1)

    def test_installed_h3_extract(self):
        if not component_command('h3', SETTINGS)[1]:
            self.skipTest('MiniMax H3 component unavailable')
        with self.runtime() as (root, runtime, actor, session):
            result = self.run_job(runtime, actor, session, 'h3_extract_frame', {'video': self.fixture(root), 'time': 0.25})
            self.assertTrue(Path(result['image']).is_file())

    def test_installed_stereo_package(self):
        if not component_command('stereo', SETTINGS)[1]:
            self.skipTest('Stereo 3D component unavailable')
        with self.runtime() as (root, runtime, actor, session):
            source = self.fixture(root)
            result = self.run_job(runtime, actor, session, 'stereo_package_stereo',
                                  {'left_video': source, 'right_video': source})
            self.assertTrue(Path(result['video_file']).is_file())

    def test_model_roots(self):
        from comfy_engine import model_paths
        with TemporaryDirectory() as folder:
            external = Path(folder).resolve()
            model = external / 'character.safetensors'
            model.touch()
            settings = {**SETTINGS, 'modelLocations': [{'path': str(model), 'category': 'loras'}]}
            self.assertEqual(model_paths(settings), {'video_studio_0': {'loras': str(external)}})

    def test_update_rollback_preserves_data(self):
        from updates import activate
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/settings.json').write_text('keep')
            (root / 'app').mkdir()
            (root / 'app/version').write_text('old')
            pending = root / 'runtime/update/pending/app'
            pending.mkdir(parents=True)
            (pending / 'version').write_text('new')
            activate(root, 'apply')
            self.assertEqual((root / 'app/version').read_text(), 'new')
            activate(root, 'rollback')
            self.assertEqual((root / 'app/version').read_text(), 'old')
            self.assertEqual((root / 'data/settings.json').read_text(), 'keep')

    def test_installed_cyvr_resize(self):
        if not component_command('cyvr', SETTINGS)[1]:
            self.skipTest('CyVR Video Enhancer component unavailable')
        with self.runtime() as (root, runtime, actor, session):
            result = self.run_job(runtime, actor, session, 'cyvr_enhance',
                                  {'source': self.fixture(root), 'rtx_vsr': False,
                                   'size_mode': 'exact', 'width': 320, 'height': 192, 'format': 'prores_422'})
            self.assertEqual((result['width'], result['height'], result['codec']), (320, 192, 'prores'))

    def test_installed_gaussian_analysis(self):
        if not component_command('gaussian', SETTINGS)[1]:
            self.skipTest('VideoToGaussian component unavailable')
        with self.runtime() as (root, runtime, actor, session):
            result = self.run_job(runtime, actor, session, 'gaussian_analyze_video', {'video_file': self.fixture(root)})
            self.assertTrue(Path(result['report_file']).is_file())


if __name__ == '__main__':
    unittest.main()
