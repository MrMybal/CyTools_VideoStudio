"""Process video on disk with bounded FFmpeg decoding, filters and encoding."""
import json
import os
import re
import subprocess

from cytools_core import CyToolError

from process_tracking import track
from studio_settings import binary

VIDEO_FORMATS = 'mov,matroska,webm,avi,mpegts,asf,mxf,mpeg,flv,mp4'
PACKING = {'sbs': (2, 1), 'hsbs': (2, 1), 'tb': (1, 2), 'htb': (1, 2), 'mono': (1, 1)}
CODECS = {'h264': ('libx264', '.mp4'), 'hevc': ('libx265', '.mp4'), 'prores': ('prores_ks', '.mov')}


def invoke(context, settings, program, arguments, *, progress_seconds=0):
    tool = binary(program, settings)
    if not tool:
        raise CyToolError('MissingDependency', f'{program} is missing. Configure FFmpeg in Components.')
    log_path = context.path(f'media-{program}.log')
    stdout_path = context.path(f'media-{program}.out')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with log_path.open('wb') as errors, stdout_path.open('wb') as output:
        process = subprocess.Popen([tool, *map(str, arguments)], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=errors, creationflags=flags)
        try:
            track(context, process)
            with stdout_path.open('rb') as updates:
                while process.poll() is None:
                    context.cancellation.sleep(0.15)
                    if progress_seconds:
                        times = re.findall(rb'^out_time_us=(\d+)$', updates.read(16384), re.M)
                        if times:
                            percent = min(97, 4 + 92 * int(times[-1]) / 1e6 / progress_seconds)
                            context.progress(percent, currentStep='Encoding')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    if process.returncode:
        with log_path.open('rb') as errors:
            errors.seek(max(0, log_path.stat().st_size - 5000), os.SEEK_SET)
            raise CyToolError('MediaFailed', errors.read().decode('utf-8', 'replace').strip()
                              or f'{program} returned exit code {process.returncode}')


def probe(context, settings, source):
    invoke(context, settings, 'ffprobe', [
        '-v', 'error', '-protocol_whitelist', 'file', '-format_whitelist', VIDEO_FORMATS,
        '-show_streams', '-show_format', '-of', 'json', '-i', source,
    ])
    result = context.path('media-ffprobe.out')
    if result.stat().st_size > 4 * 1024 ** 2:
        raise CyToolError('InputTooLarge', 'Video metadata exceeds 4 MiB.')
    data = json.loads(result.read_text('utf-8'))
    streams = data.get('streams', [])
    video = next((row for row in streams if row['codec_type'] == 'video'
                  and not row.get('disposition', {}).get('attached_pic')), None)
    if not video or not video.get('width') or not video.get('height'):
        raise CyToolError('InvalidVideo', 'No playable video stream was found.')
    for row in (video, data.get('format', {})):
        try:
            duration = max(0, float(row.get('duration', 0)))
            if duration:
                break
        except (ValueError, TypeError):
            duration = 0
    return dict(width=video['width'], height=video['height'], codec=video['codec_name'],
                duration=duration, streams=streams), video['index']


def media_args(source, start, threads=2):
    return [
        '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-filter_threads', str(threads), '-filter_complex_threads', str(threads),
        '-threads', str(threads), '-ss', str(start), '-protocol_whitelist', 'file',
        '-format_whitelist', VIDEO_FORMATS, '-i', source,
    ]


def preview(context, settings, p):
    info, index = probe(context, settings, p['source'])
    if info['duration'] and p['time'] >= info['duration']:
        raise CyToolError('InvalidParameters', 'Preview time must be inside the video.')
    target = context.path('outputs/preview.png')
    invoke(context, settings, 'ffmpeg', [
        *media_args(p['source'], p['time']), '-map', f'0:{index}', '-frames:v', '1',
        '-vf', f"scale='min({p['max_width']},iw)':'min({p['max_width']},ih)':force_original_aspect_ratio=decrease",
        '-threads', '1', target,
    ])
    if not target.is_file():
        raise CyToolError('InvalidVideo', 'No frame was decoded at this timestamp.')
    return {'image_file': str(target)}


def intended_geometry(p, info):
    columns, rows = PACKING[p['layout']]
    width, height = p.get('width'), p.get('height')
    if width is None and height is None:
        width, height = info['width'], info['height']
    elif width is None:
        width = round(height * info['width'] / info['height'])
    elif height is None:
        height = round(width * info['height'] / info['width'])
    width, height = width // (2 * columns) * (2 * columns), height // (2 * rows) * (2 * rows)
    if not 2 * columns <= width <= 8192 or not 2 * rows <= height <= 8192:
        raise CyToolError('InvalidParameters', 'The output size must fit the selected packing and an 8192-pixel canvas.')
    return width, height


def scale_filter(p, width, height):
    scale = f"scale={width}:{height}:flags={p['filter']}"
    if p['fit'] == 'contain':
        return (scale + ':force_original_aspect_ratio=decrease:force_divisible_by=2,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2')
    if p['fit'] == 'cover':
        return (scale + ':force_original_aspect_ratio=increase:force_divisible_by=2,'
                f'crop={width}:{height}')
    return scale


def graph(p, width, height, index):
    suffix = ',setsar=1' + (f",fps={p['fps']}" if 'fps' in p else '')
    columns, rows = PACKING[p['layout']]
    if (columns, rows) == (1, 1):
        return f'[0:{index}]' + scale_filter(p, width, height) + suffix + '[v]'
    scale = scale_filter(p, width // columns, height // rows)
    w, h = f'iw/{columns}', f'ih/{rows}'
    offset = f'{w}:0' if columns == 2 else f'0:{h}'
    eyes = '[right][left]' if p['swap_eyes'] else '[left][right]'
    stack = 'hstack' if columns == 2 else 'vstack'
    return (f'[0:{index}]split[a][b];[a]crop={w}:{h}:0:0:exact=1,{scale}[left];'
            f'[b]crop={w}:{h}:{offset}:exact=1,{scale}[right];{eyes}{stack}{suffix}[v]')


def transcode(context, settings, p):
    info, index = probe(context, settings, p['source'])
    start = p['start']
    end = p.get('end', info['duration'])
    if ('end' in p and end <= start) or (info['duration'] and start >= info['duration']):
        raise CyToolError('InvalidParameters', 'The trim range must include part of the video.')
    duration = max(0, min(end, info['duration'] or end) - start)
    width, height = intended_geometry(p, info)
    context.progress(3, currentStep=f'Planned video: {width} × {height}')
    threads = min(p['threads'], context.runtime.system['cpuThreads'])
    codec, suffix = CODECS[p['codec']]
    target = context.path('outputs/video' + suffix)
    if p['codec'] == 'prores':
        codec_options = ['-profile:v', '2', '-pix_fmt', 'yuv422p10le']
    else:
        codec_options = ['-crf', p['quality'], '-preset', p['speed'], '-pix_fmt', 'yuv420p']
        if p['codec'] == 'hevc':
            codec_options += ['-tag:v', 'hvc1', '-x265-params', f'pools={threads}:frame-threads=1']
    audio = ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', '192k'] if p['audio'] == 'encode' else ['-an']
    invoke(context, settings, 'ffmpeg', [
        *media_args(p['source'], start, threads), '-filter_complex', graph(p, width, height, index),
        '-map', '[v]', *audio, '-c:v', codec, '-threads', threads, *codec_options,
        *(['-t', duration] if duration else []), '-progress', 'pipe:1', target,
    ], progress_seconds=duration)
    result, _ = probe(context, settings, target)
    result.pop('streams')
    return {'video_file': str(target), **result}


def animate_still(context, settings, p):
    width, height, fps = p['width'], p['height'], p['fps']
    frames = max(2, round(p['seconds'] * fps))
    ratio = f'on/{frames - 1}'
    easing = ratio if p['easing'] == 'linear' else f'({ratio})*({ratio})*(3-2*({ratio}))'

    def keyframe(key):
        start, end = p[f'from_{key}'], p[f'to_{key}']
        return f'{start}+({end}-{start})*({easing})'

    # Oversample the working image to reduce integer-coordinate jitter in zoompan.
    canvas_width, canvas_height = width * 2, height * 2
    zoompan = (f"scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=increase:flags=bicubic,"
               f"crop={canvas_width}:{canvas_height},zoompan=z='{keyframe('zoom')}':"
               f"x='(iw-iw/zoom)*({keyframe('x')})':y='(ih-ih/zoom)*({keyframe('y')})':"
               f"d={frames}:s={width}x{height}:fps={fps},setsar=1")
    target = context.path('outputs/motion.mp4')
    context.progress(3, currentStep=f'Planned video: {width} × {height}')
    invoke(context, settings, 'ffmpeg', [
        '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads', '2', '-filter_threads', '2',
        '-protocol_whitelist', 'file', '-format_whitelist', 'png_pipe,jpeg_pipe,webp_pipe',
        '-i', p['image'], '-vf', zoompan, '-frames:v', frames,
        '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
        '-threads', '2', '-progress', 'pipe:1', target,
    ], progress_seconds=frames / fps)
    result, _ = probe(context, settings, target)
    result.pop('streams')
    return {'video_file': str(target), **result}
