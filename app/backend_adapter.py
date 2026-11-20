"""Run installed products in their own Python, binaries and dependency environment."""
import json
import subprocess
import time

from cytools_core import CyToolError

from process_tracking import track
from studio_settings import APP, component_command

def run(context, settings, component, operation, parameters, *, preset=None):
    root, python = component_command(component, settings)
    if python is None:
        raise CyToolError('MissingDependency', f'Link or install the {component} component in Components.',
                          recoverable=True)
    exchange = context.path('adapter/request.json')
    response = exchange.with_name('response.json')
    progress_path = exchange.with_name('progress.json')
    cancel_path = exchange.with_name('cancel')
    backend = preset or {}
    exchange.write_text(json.dumps({
        'operation': operation, 'parameters': parameters, 'backendId': backend.get('backendId', ''),
        'modelId': backend.get('modelId', ''),
    }), 'utf-8')
    context.cancellation.check()
    with context.path('adapter/backend.log').open('wb') as log:
        process = subprocess.Popen([str(python), '-B', str(APP / 'backend_process.py'),
                                    component, str(root), str(exchange)],
                                   cwd=context.workspace, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        track(context, process)
        last = None
        cancellation = None
        next_check = 0
        while process.poll() is None:
            if not cancellation:
                try:
                    context.cancellation.check()
                except CyToolError as error:
                    cancellation = error
                    cancel_path.touch()
            if cancellation is None and time.monotonic() >= next_check:
                next_check = time.monotonic() + 0.3
                try:
                    value = json.loads(progress_path.read_text('utf-8'))
                    if value != last:
                        context.progress(min(99, value['percent']), currentStep=value.get('currentStep', 'Processing'))
                        last = value
                except (OSError, ValueError, KeyError):
                    pass
                except CyToolError as error:
                    cancellation = error
                    cancel_path.touch()
            time.sleep(0.15)
        process.wait()
    if cancellation:
        raise cancellation
    if not response.is_file():
        raise CyToolError('WorkerCrashed', f'{component} exited with code {process.returncode}. See adapter/backend.log.')
    result = json.loads(response.read_text('utf-8'))
    if 'error' in result:
        error = result['error']
        raise CyToolError(error.get('code', 'BackendFailed'), error.get('message', 'Component failed.'),
                          recoverable=error.get('recoverable', False),
                          details=error.get('technicalDetails', error.get('details')))
    return result['outputs']
