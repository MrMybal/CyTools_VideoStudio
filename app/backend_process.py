"""One invocation in a companion product interpreter."""
import importlib
import json
import os
import sys
import threading
from pathlib import Path

TERMINAL = {'Completed', 'Failed', 'Cancelled'}


def atomic(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, default=str), 'utf-8')
    temp.replace(path)


def run_cyvr(root, exchange, parameters):
    sys.path.insert(0, str(root))
    from rtxvsr.runtime import activate_runtime
    activate_runtime()
    from rtxvsr.core import ffmpeg_io
    from rtxvsr.core.gpu_backend import get_backend
    from rtxvsr.core.jobspec import EncodeSettings, JobSpec
    from rtxvsr.core.pipeline import resolve_plan, run_job

    source = Path(parameters.pop('source'))
    output = exchange.parent.parent / 'outputs' / 'enhanced'
    output.parent.mkdir(parents=True, exist_ok=True)
    progress = exchange.with_name('progress.json')
    cancel = threading.Event()
    stop = threading.Event()

    def watch():
        while not stop.wait(0.15):
            if exchange.with_name('cancel').exists():
                cancel.set()
                return

    def report(value):
        if value.stage == 'processing':
            atomic(progress, {'percent': 4 + 93 * max(0, min(1, value.fraction)), 'currentStep': 'Enhancing video'})
    encode = EncodeSettings(format=parameters.pop('format'), preset=parameters.pop('encoder_preset'),
                            cq=parameters.pop('cq'), bframes=parameters.pop('bframes', None))
    device = parameters.pop('gpu_backend')
    parameters.pop('vram_gib')
    parameters['start_seconds'] = parameters.pop('start')
    parameters['end_seconds'] = parameters.pop('end', None)
    spec = JobSpec(source=source, output=output.with_suffix(encode.suffix), encode=encode, **parameters)
    plan = resolve_plan(spec, ffmpeg_io.probe(source))
    atomic(progress, {'percent': 2, 'currentStep': f'Planned video: {plan.output_width} × {plan.output_height}'})
    monitor = threading.Thread(target=watch)
    monitor.start()
    try:
        backend = get_backend(device) if (spec.rtx_vsr or spec.dlss_sr or spec.dlss5 or encode.resolve().hardware) else None
        target = run_job(spec, backend=backend, progress=report, cancel=cancel, resume=False)
    finally:
        stop.set()
        monitor.join()
    info = ffmpeg_io.probe(target)
    return {'video_file': str(target), 'width': info.width, 'height': info.height,
            'duration': info.duration, 'codec': info.codec}


def run_legacy(component, root, exchange, request):
    sys.path.insert(0, str(root / 'app'))
    os.chdir(root / 'app')
    from cytools_core import CyToolError
    tool = importlib.import_module('tool')
    runtime_dir = exchange.parent.parent / 'engine'
    operation = request['operation']
    parameters = request['parameters']

    # The ComfyFlow import is allowed to persist workflow metadata. Node installation
    # stays in the dedicated component's installer.
    if operation == 'import_workflow' and (parameters.get('install_node_packs') or parameters.get('download_models')):
        raise CyToolError('AccessDenied', 'Install ComfyFlow components in its desktop application.')

    with tool.create_runtime(runtime_dir) as runtime:
        fields = dict(runtime.input_fields)
        fields.pop(operation, None)
        runtime.configure_inputs(runtime_dir / 'inputs', fields=fields)
        operation_descriptor = runtime.operation(operation)
        permissions = operation_descriptor.get('permissions', ())
        principal = runtime.authenticate(runtime.create_client('VideoStudio worker', permissions=permissions)['token'])
        session = runtime.create_session(principal, temporary=False)['sessionId']
        if exchange.with_name('cancel').exists():
            raise CyToolError('Cancelled', 'Operation cancelled before dispatch.')
        job = runtime.submit(principal, session, operation, parameters,
                             backend_id=request['backendId'], model_id=request['modelId'])
        job_id = job['jobId']
        previous = None
        cancelled = False
        while job['state'] not in TERMINAL:
            if not cancelled and exchange.with_name('cancel').exists():
                runtime.cancel(principal, job_id)
                cancelled = True
            progress = job.get('progress')
            if progress is not None and progress != previous:
                atomic(exchange.with_name('progress.json'), progress)
                previous = progress
            try:
                job = runtime.wait(principal, job_id, timeout=0.25)
            except CyToolError as error:
                if error.code != 'Timeout':
                    raise
                job = runtime.get_job(principal, job_id)
        job = runtime.wait(principal, job_id, timeout=30)
        runtime.finish_task(principal, session)
        return {'outputs': job['outputs']} if job['state'] == 'Completed' else {'error': job['error']}


def main():
    component, root, exchange = sys.argv[1:]
    root, exchange = Path(root), Path(exchange)
    request = json.loads(exchange.read_text('utf-8'))
    try:
        result = ({'outputs': run_cyvr(root, exchange, request['parameters'])} if component == 'cyvr'
                  else run_legacy(component, root, exchange, request))
    except Exception as error:
        if hasattr(error, 'to_dict'):
            result = {'error': error.to_dict()}
        else:
            import traceback
            traceback.print_exc(file=sys.stderr)
            result = {'error': {'code': 'BackendFailed', 'message': str(error)}}
    atomic(exchange.with_name('response.json'), result)


if __name__ == '__main__':
    main()
