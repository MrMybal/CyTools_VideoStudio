"""GitHub ZIP staging and offline application rollback."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import psutil
from cytools_core.downloads import DownloadManager
from cytools_core.storage import RuntimeLock

from model_setup import unpack
from studio_settings import ROOT


def stage(url, digest, cancel, progress):
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.hostname) != ('https', 'github.com') or parsed.query or parsed.fragment or not re.fullmatch(
        r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/[^/]+/[^/]+\.zip', parsed.path):
        raise ValueError('Use the HTTPS URL of a GitHub release ZIP.')
    if not re.fullmatch(r'[A-Fa-f0-9]{64}', digest):
        raise ValueError('Enter the published SHA-256 before downloading.')
    updates = ROOT / 'runtime/update'
    updates.mkdir(parents=True, exist_ok=True)
    lock = RuntimeLock(updates / 'update.lock')
    try:
        name = f'update-{digest.lower()}.zip'
        response = DownloadManager(updates / 'download').download(name, url, sha256=digest,
                                                                   cancellation=cancel, progress=progress)
        with TemporaryDirectory(dir=updates) as work:
            directory = Path(work).resolve()
            unpack(response['path'], directory, max_size=512 * 1024 ** 2)
            app = directory / 'app'
            manifest = json.loads((app / 'CyTool.json').read_text('utf-8'))
            if manifest.get('id') != 'cy.tool.videostudio' or not (app / 'desktop.py').is_file():
                raise ValueError('This release does not contain a recognized VideoStudio application.')
            pending = updates / 'pending'
            pending.mkdir(exist_ok=True)
            if (pending / 'app').exists():
                raise ValueError('Activate the staged update before staging another.')
            app.rename(pending / 'app')
            return manifest['version']
    finally:
        lock.close()


def activate(root, action):
    root = root.resolve()
    updates = root / 'runtime/update'
    pending, previous = updates / 'pending/app', updates / 'previous'
    app = root / 'app'
    if action not in ('apply', 'rollback'):
        raise ValueError('Unknown update action.')
    replacement = pending if action == 'apply' else previous
    for folder in (app, replacement, updates):
        if not folder.is_dir() or folder.resolve() != folder:
            raise ValueError(f'Invalid update folder: {folder}')
    lock = RuntimeLock(updates / 'update.lock')
    try:
        parked = updates / 'switching'
        if parked.exists():
            raise RuntimeError('An incomplete application switch needs recovery. See runtime/update.')
        if action == 'apply' and previous.exists() and previous.resolve() != previous:
            raise ValueError('Rollback folder has been redirected.')
        app.rename(parked)
        try:
            replacement.rename(app)
        except BaseException:
            parked.rename(app)
            raise
        if action == 'apply' and previous.exists():
            shutil.rmtree(previous)
        parked.rename(previous)
    finally:
        lock.close()


def schedule(action):
    update_dir = ROOT / 'runtime/update'
    update_dir.mkdir(parents=True, exist_ok=True)
    worker = update_dir / 'updater.py'
    for name in ('updates.py', 'model_setup.py', 'studio_settings.py'):
        target = worker if name == 'updates.py' else update_dir / name
        shutil.copyfile(Path(__file__).with_name(name), target)
    with (update_dir / 'update.log').open('ab') as log:
        subprocess.Popen([sys.executable, str(worker), action, str(ROOT), str(os.getpid()),
                          str(psutil.Process().create_time())], cwd=update_dir, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('apply', 'rollback'))
    parser.add_argument('root', type=Path)
    parser.add_argument('pid', type=int)
    parser.add_argument('created', type=float)
    args = parser.parse_args()
    try:
        process = psutil.Process(args.pid)
        if process.create_time() == args.created:
            process.wait(timeout=90)
    except psutil.NoSuchProcess:
        pass
    deadline = time.monotonic() + 6
    while True:
        try:
            activate(args.root.resolve(), args.action)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.3)


if __name__ == '__main__':
    main()
