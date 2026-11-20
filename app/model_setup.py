"""Optional component setup initiated from the desktop by the operator."""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from cytools_core.downloads import DownloadManager

from studio_settings import ROOT, absolute

COMFY_REPOSITORY = 'https://github.com/Comfy-Org/ComfyUI'
TORCH_OPTIONS = {'default': '', 'cpu': 'https://download.pytorch.org/whl/cpu',
                 'cu128': 'https://download.pytorch.org/whl/cu128',
                 'cu130': 'https://download.pytorch.org/whl/cu130'}
MODEL_SUFFIXES = ('.safetensors', '.onnx', '.gguf', '.pt', '.pth', '.ckpt', '.bin', '.npz')


def model_location(text, category):
    path = absolute(text)
    if not path.exists() or (category == 'library' and not path.is_dir()):
        raise ValueError('Choose an existing file or folder. A model library is a folder.')
    return {'path': str(path), 'category': category}


def download_model(url, name, folder, category, sha256, accepted, cancel, status):
    if not accepted:
        raise ValueError('Confirm the model publisher’s license first.')
    if not urlsplit(url).hostname or urlsplit(url).query or urlsplit(url).fragment:
        raise ValueError('Use a direct public HTTPS file URL without credentials or temporary tokens.')
    if Path(name).suffix.lower() not in MODEL_SUFFIXES:
        raise ValueError('Choose a supported model filename extension.')
    if sha256 and not re.fullmatch('[A-Fa-f0-9]{64}', sha256):
        raise ValueError('SHA-256 must contain 64 hexadecimal digits.')
    target = absolute(folder) if folder else ROOT / 'data/models' / category
    manager = DownloadManager(target)
    if (target / Path(name).name).exists() and not (sha256 and manager.verify(name, sha256=sha256)):
        raise ValueError('A different file with this name already exists. Choose a new filename.')
    result = manager.download(name, url, sha256=sha256 or None, cancellation=cancel,
                              progress=lambda done, total: status(done, total), license_accepted=accepted)
    return model_location(result['path'], category)


def unpack(archive, destination, *, max_size):
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 30000 or sum(row.file_size for row in entries) > max_size:
            raise ValueError('Archive exceeds the supported extraction size.')
        seen = set()
        for row in entries:
            path = Path(row.filename)
            normalized = row.filename.replace('\\', '/').rstrip('/').casefold()
            if (not normalized or normalized in seen or path.is_absolute() or ':' in row.filename
                    or '\\' in row.filename or any(part in ('..', '.') for part in path.parts)
                    or not (destination / path).resolve().is_relative_to(destination)
                    or (row.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f'Unsafe or duplicate archive entry: {row.filename}')
            seen.add(normalized)
        source.extractall(destination)


def run_setup(command, cancel, log, cwd):
    from comfy_engine import stop_process

    with log.open('ab') as output:
        process = subprocess.Popen([str(part) for part in command], cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while process.poll() is None:
                cancel.sleep(0.25)
        finally:
            stop_process(process)
    if process.returncode:
        raise RuntimeError(f'Installation command failed ({process.returncode}). See {log}.')


def install_comfy(tag, torch, accepted, cancel, status):
    """Install an official source release and its Python packages into private folders."""
    if not accepted:
        raise ValueError('Confirm the source and dependency licenses first.')
    if not re.fullmatch(r'v\d{1,4}\.\d{1,4}\.\d{1,4}', tag):
        raise ValueError('Choose an official ComfyUI release tag, e.g. v0.36.0.')
    runtime = ROOT / 'runtime'
    config_dir = runtime / 'comfy-releases' / tag
    config_dir.mkdir(parents=True, exist_ok=True)
    source, python_dir = config_dir / 'source', config_dir / 'python'
    log = config_dir / 'install.log'
    archive_name = f'comfyui-{tag}.zip'
    if not (source / 'main.py').is_file():
        download = DownloadManager(runtime / 'downloads').download(
            archive_name, f'{COMFY_REPOSITORY}/archive/refs/tags/{tag}.zip',
            cancellation=cancel, progress=status, license_accepted=accepted)
        destination = config_dir / 'extract'
        destination.mkdir(exist_ok=True)
        unpack(download['path'], destination, max_size=512 * 1024 ** 2)
        folders = [item for item in destination.iterdir() if item.is_dir() and (item / 'main.py').is_file()]
        if len(folders) != 1:
            raise RuntimeError('Unexpected ComfyUI source archive.')
        if source.exists():
            raise RuntimeError('ComfyUI source path is already occupied.')
        folders[0].rename(source)
    executable = python_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        run_setup([sys.executable, '-m', 'venv', python_dir], cancel, log, runtime)
    if TORCH_OPTIONS[torch]:
        run_setup([executable, '-m', 'pip', 'install', '--index-url', TORCH_OPTIONS[torch],
                   'torch', 'torchvision', 'torchaudio'], cancel, log, runtime)
    run_setup([executable, '-m', 'pip', 'install', '-r', source / 'requirements.txt'], cancel, log, runtime)
    run_setup([executable, source / 'main.py', '--help'], cancel, log, source)
    return {'root': str(source), 'python': str(executable), 'mode': 'managed',
            **({'device': 'cpu'} if torch == 'cpu' else {})}
