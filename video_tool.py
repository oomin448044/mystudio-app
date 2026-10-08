"""Video transform engine: freeze frames + horizontal flip + subtle color shift.

Personal-use tool, no limits / no credits.
Uses ffmpeg only. One encode pass, audio kept in sync (silence inserted
wherever a freeze frame is added).
"""
import json
import os
import subprocess
import tempfile


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-3000:])
    return r


def _run_progress(cmd, total_us, progress_cb):
    """Run ffmpeg with -progress pipe:1; call progress_cb(0..100)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, bufsize=1)
    try:
        for line in p.stdout:
            line = line.strip()
            if line.startswith('out_time_us='):
                try:
                    us = int(line.split('=', 1)[1])
                except ValueError:
                    continue
                if total_us > 0 and progress_cb:
                    progress_cb(max(0.0, min(99.0, us / total_us * 100.0)))
            elif line == 'progress=end' and progress_cb:
                progress_cb(100.0)
    finally:
        err = p.stderr.read()
        p.wait()
    if p.returncode != 0:
        raise RuntimeError((err or '')[-3000:] or 'ffmpeg failed')
    if progress_cb:
        progress_cb(100.0)


def probe_duration(path):
    r = _run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
              '-of', 'json', path])
    return float(json.loads(r.stdout)['format']['duration'])


def probe_has_audio(path):
    r = _run(['ffprobe', '-v', 'error', '-select_streams', 'a',
              '-show_entries', 'stream=index', '-of', 'json', path])
    return len(json.loads(r.stdout).get('streams', [])) > 0


def transform(src, dst, freeze_every=5.0, freeze_dur=0.5,
              do_flip=True, do_color=True,
              logo_path=None, logo_opacity=0.25, progress_cb=None):
    """Apply transforms and write dst (mp4).

    freeze_every: insert a freeze frame every N seconds (0 = off)
    freeze_dur:   how long each freeze frame holds (seconds)
    do_flip:      mirror horizontally (hflip)
    do_color:     subtle brightness/contrast/saturation/hue shift
    logo_path:    PNG/JPG logo -> roaming faint watermark (None = off)
    logo_opacity: 0..1, how visible the logo is (0.25 = faint)
    progress_cb:  optional callable receiving 0..100 as encode progresses
    """
    dur = probe_duration(src)
    has_audio = probe_has_audio(src)

    marks = []
    if freeze_every and freeze_every > 0:
        m = float(freeze_every)
        while m < dur - 0.2:
            marks.append(m)
            m += float(freeze_every)

    tmpd = tempfile.mkdtemp(prefix='vt_')
    stills = []
    try:
        for i, mk in enumerate(marks):
            p = os.path.join(tmpd, f'still_{i}.png')
            # -ss before -i = fast seek (keyframe-accurate enough for freezes)
            _run(['ffmpeg', '-y', '-ss', f'{mk:.3f}', '-i', src,
                  '-frames:v', '1', '-q:v', '3', p])
            stills.append(p)

        bounds = [0.0] + marks + [dur]
        inputs, pre, parts = [], [], []
        idx = 0
        seg = 0
        for s in range(len(bounds) - 1):
            st, en = bounds[s], bounds[s + 1]
            seg_d = en - st
            if seg_d < 0.05:
                continue
            inputs += ['-ss', f'{st:.3f}', '-t', f'{seg_d:.3f}', '-i', src]
            vi = idx
            idx += 1
            pre.append(f'[{vi}:v]fps=30,format=yuv420p,settb=AVTB[v{seg}];')
            if has_audio:
                pre.append(f'[{vi}:a]aresample=44100,'
                           f'aformat=channel_layouts=stereo[a{seg}];')
            else:
                inputs += ['-f', 'lavfi', '-t', f'{seg_d:.3f}', '-i',
                           'anullsrc=r=44100:cl=stereo']
                ai = idx
                idx += 1
                pre.append(f'[{ai}:a]aresample=44100,'
                           f'aformat=channel_layouts=stereo[a{seg}];')
            parts += [f'[v{seg}]', f'[a{seg}]']

            if s < len(stills):
                fd = float(freeze_dur)
                inputs += ['-loop', '1', '-framerate', '30',
                           '-t', f'{fd}', '-i', stills[s]]
                si = idx
                idx += 1
                pre.append(f'[{si}:v]fps=30,format=yuv420p,'
                           f'settb=AVTB[fv{seg}];')
                inputs += ['-f', 'lavfi', '-t', f'{fd}', '-i',
                           'anullsrc=r=44100:cl=stereo']
                sai = idx
                idx += 1
                pre.append(f'[{sai}:a]aresample=44100,'
                           f'aformat=channel_layouts=stereo[fa{seg}];')
                parts += [f'[fv{seg}]', f'[fa{seg}]']
            seg += 1

        n = len(parts) // 2
        filt = ''.join(pre) + ''.join(parts) + \
            f'concat=n={n}:v=1:a=1[cv][ca]'

        vf = ''
        if do_flip:
            vf += 'hflip,'
        if do_color:
            # subtle shift so it no longer matches the original
            vf += ('eq=brightness=0.04:contrast=1.06:saturation=1.12,'
                   'hue=h=6')
        vf = vf.rstrip(',')
        filt = ''.join(pre) + ''.join(parts) + \
            f'concat=n={n}:v=1:a=1[cv][ca]'
        vcur = '[cv]'
        if vf:
            filt += f';{vcur}{vf}[v1]'
            vcur = '[v1]'
        if logo_path and os.path.exists(logo_path):
            # single-frame input; overlay repeats it (eof_action=repeat default)
            inputs += ['-i', logo_path]
            li = idx
            idx += 1
            op = max(0.0, min(1.0, float(logo_opacity)))
            pre_logo = (f'[{li}:v]scale=140:-1,format=rgba,'
                        f'colorchannelmixer=aa={op}[lg];')
            # roaming watermark: smooth wandering path, evaluated per frame
            ov = ("overlay="
                  "x='(W-w)*(0.5+0.5*sin(2*PI*t/17))':"
                  "y='(H-h)*(0.5+0.5*cos(2*PI*t/23))'")
            filt = pre_logo + filt + f';{vcur}[lg]{ov}[vout]'
            vmap = '[vout]'
        else:
            vmap = vcur

        total_dur = dur + float(freeze_dur) * len(marks)
        cmd = (['ffmpeg', '-y', '-hide_banner', '-v', 'error',
                '-progress', 'pipe:1', '-nostats'] + inputs +
               ['-filter_complex', filt,
                '-map', vmap, '-map', '[ca]',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '128k',
                '-movflags', '+faststart', dst])
        if progress_cb:
            _run_progress(cmd, int(total_dur * 1_000_000), progress_cb)
        else:
            _run(cmd)
    finally:
        for p in stills:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpd)
        except OSError:
            pass
    return dst
