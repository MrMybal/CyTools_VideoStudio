"""Assemble a small GitHub release ZIP without users' data or model runtimes."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from studio_settings import APP, ROOT


def package(output, platform, core, cyui):
    if not core.is_file() or not cyui.is_file():
        raise ValueError('Provide the platform CyToolsCore wheel and the CyUI wheel.')
    platform_tag = 'win_amd64' if platform == 'windows' else 'linux'
    if platform_tag not in core.name or not core.name.startswith('cytools_core-0.12.0-'):
        raise ValueError('CyToolsCore wheel must match the target OS and SDK version.')
    launcher = 'CyTools_VideoStudio.exe' if platform == 'windows' else 'CyTools_VideoStudio.sh'
    if not (ROOT / launcher).is_file():
        raise FileNotFoundError(launcher)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in (launcher, 'LICENSE', 'LISEZ-MOI.md', 'README.md', 'README.fr.md',
                     'assets/screenshots/windows-video.png', 'assets/screenshots/windows-components-fr.png'):
            archive.write(ROOT / name, name)
        for path in sorted(APP.rglob('*')):
            relative = path.relative_to(APP)
            if path.is_file() and not any(part.startswith(('.', '__')) for part in relative.parts):
                if relative.parts[0] != 'Docs' and path.suffix.lower() in ('.json', '.md', '.py', '.ps1', '.sh', '.txt', '.cpp'):
                    archive.write(path, 'app/' + relative.as_posix())
        for wheel in (core, cyui):
            archive.write(wheel, 'runtime/packages/' + wheel.name)
        for path in (ROOT / 'LICENCES').glob('*.txt'):
            archive.write(path, 'LICENCES/' + path.name)
        archive.writestr(ZipInfo('data/'), b'')
    with output.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', 'utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('windows', 'linux'), required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--cyui', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    version = json.loads((APP / 'CyTool.json').read_text('utf-8'))['version']
    path = args.output or ROOT / 'runtime/releases' / f'CyTools_VideoStudio-{version}-{args.platform}-x64.zip'
    path.parent.mkdir(parents=True, exist_ok=True)
    package(path, args.platform, args.core, args.cyui)
    print(path)


if __name__ == '__main__':
    main()
