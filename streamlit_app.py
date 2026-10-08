"""My Studio — Streamlit edition (personal use, no limits).

Same engine as the Flask app: upload a video file OR paste a video link
(direct mp4 / TikTok / YouTube via yt-dlp) -> transform
(freeze frames + mirror flip + subtle color shift + roaming logo watermark,
audio kept in sync) -> download the result.

Deploy: push this folder to GitHub, then connect the repo at
share.streamlit.io (New app). ffmpeg comes from packages.txt.
"""
import os
import re
import subprocess
import tempfile
import urllib.request

import requests
import streamlit as st

from video_tool import transform

st.set_page_config(page_title='My Studio', page_icon='🎬', layout='centered')

# ---------- helpers ----------

def fetch_url_video(url, dest_dir, progress_cb=None):
    """Download a video from a URL. Returns local path."""
    base = os.path.join(dest_dir, 'src')
    if re.search(r'\.(mp4|mov|webm|mkv)(\?|#|$)', url, re.I):
        ext = re.search(r'\.(mp4|mov|webm|mkv)', url, re.I).group(1).lower()
        dest = f'{base}.{ext}'
        urllib.request.urlretrieve(url, dest)
        if progress_cb:
            progress_cb(100.0)
        return dest
    tmpl = base + '.%(ext)s'
    cmd = ['yt-dlp', '-f', 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b',
           '--merge-output-format', 'mp4', '--no-playlist',
           '--newline', '-o', tmpl, url]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    pct_re = re.compile(r'\[download\]\s+(\d+(?:\.\d+)?)%')
    tail = []
    for line in p.stdout:
        tail.append(line)
        if len(tail) > 30:
            tail.pop(0)
        m = pct_re.search(line)
        if m and progress_cb:
            try:
                progress_cb(min(99.0, float(m.group(1))))
            except ValueError:
                pass
    rc = p.wait()
    if rc != 0:
        raise RuntimeError('link download မရဘူး — ' +
                           (''.join(tail)[-200:] or 'unknown error'))
    if progress_cb:
        progress_cb(100.0)
    for f in os.listdir(dest_dir):
        if f.startswith('src.'):
            return os.path.join(dest_dir, f)
    raise RuntimeError('downloaded file not found')


def call_user_api(endpoint, key, src_path, prompt):
    """POST video to the user's own API endpoint. Returns result video path."""
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    with open(src_path, 'rb') as fh:
        resp = requests.post(
            endpoint, headers=headers,
            files={'video': (os.path.basename(src_path), fh, 'video/mp4')},
            data={'prompt': prompt}, timeout=1800)
    if resp.status_code >= 400:
        raise RuntimeError(f'API error {resp.status_code}: '
                           f'{resp.text[:200]}')
    gen = os.path.join(tempfile.gettempdir(), 'gen_result.mp4')
    ct = resp.headers.get('Content-Type', '')
    if 'video' in ct or 'octet-stream' in ct:
        with open(gen, 'wb') as fh:
            fh.write(resp.content)
    else:
        url = resp.json().get('video_url') or resp.json().get('url')
        if not url:
            raise RuntimeError('API response မှာ video url မပါဘူး: '
                               f'{resp.text[:200]}')
        urllib.request.urlretrieve(url, gen)
    return gen


# ---------- UI ----------

st.markdown("<h1 style='text-align:center'>🎬 My Studio</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;color:#6b7488'>"
            "Video တိုင်း။ ကိုယ့်ပုံစံအတိုင်း။</p>",
            unsafe_allow_html=True)

tab1, tab2 = st.tabs(['📁 File', '🔗 Link'])
src_file = None
src_url = ''
with tab1:
    up = st.file_uploader('Video file ရွေး', type=['mp4', 'mov', 'webm', 'mkv'])
    if up:
        src_file = up
with tab2:
    src_url = st.text_input('Video link ထည့် (TikTok / YouTube / mp4)',
                            placeholder='https://…').strip()

logo_up = st.file_uploader('Logo (optional — video ပေါ်မှာ လှည့်ပတ်နေမယ်)',
                           type=['png', 'jpg', 'jpeg'])

st.subheader('⚙️ Transform ရွေးချယ်မှု')
c1, c2 = st.columns(2)
with c1:
    freeze_every = st.number_input('Freeze every (စက္ကန့်)', 0.0, 60.0, 5.0,
                                   0.5)
with c2:
    freeze_dur = st.number_input('Freeze ကြာချိန် (စက္ကန့်)', 0.1, 5.0, 0.5,
                                 0.1)
do_flip = st.checkbox('🔄 Mirror flip (မှန်ပြန်)', value=True)
do_color = st.checkbox('🎨 အရောင် အနည်းငယ် ပြောင်း', value=True)

st.divider()
run = st.button('⚙️ Transform စတင်ရန်', type='primary', use_container_width=True)

if run:
    if not src_file and not src_url:
        st.error('video file ဒါမှမဟုတ် link တစ်ခု ထည့်ပေးပါ')
        st.stop()
    tmpd = tempfile.mkdtemp(prefix='mystudio_')
    try:
        if src_file:
            ext = os.path.splitext(src_file.name)[1] or '.mp4'
            src_path = os.path.join(tmpd, f'upload{ext}')
            with open(src_path, 'wb') as fh:
                fh.write(src_file.read())
            name = src_file.name
        else:
            name = src_url
            src_path = None
        logo_path = None
        if logo_up:
            logo_path = os.path.join(tmpd, 'logo' +
                                     os.path.splitext(logo_up.name)[1])
            with open(logo_path, 'wb') as fh:
                fh.write(logo_up.read())

        bar = st.progress(0)
        label = st.empty()

        def make_cb(phase_text):
            def cb(p):
                p = max(0.0, min(100.0, float(p)))
                bar.progress(int(p))
                label.text(f'{phase_text} {p:.0f}%')
            return cb

        if src_path is None:
            src_path = fetch_url_video(src_url, tmpd,
                                       progress_cb=make_cb('⬇️ Download'))
        out = os.path.join(tmpd, 'out.mp4')
        transform(src_path, out, freeze_every=float(freeze_every),
                  freeze_dur=float(freeze_dur), do_flip=do_flip,
                  do_color=do_color, logo_path=logo_path,
                  logo_opacity=0.25,
                  progress_cb=make_cb('⚙️ Transform'))
        bar.progress(100)
        label.text('✅ ပြီးသွားပြီ!')
        with open(out, 'rb') as fh:
            st.download_button('⬇️ Video Download', fh.read(),
                               file_name=f'mystudio_{name}',
                               mime='video/mp4',
                               use_container_width=True)
        st.video(out)
    except Exception as e:  # noqa: BLE001
        st.error(f'အမှား: {e}')

# ---------- bring-your-own API ----------
st.divider()
st.subheader('🤖 ကိုယ့် API နဲ့ Generate')
with st.expander('API setting'):
    endpoint = st.text_input('API endpoint URL',
                             placeholder='https://your-api.com/generate')
    api_key = st.text_input('API key', type='password')
    prompt = st.text_area('Prompt (optional)', '')
gen_run = st.button('✨ API နဲ့ Generate', use_container_width=True)
if gen_run:
    if not endpoint.strip():
        st.error('API endpoint ထည့်ပေးပါ')
        st.stop()
    if not src_file and not src_url:
        st.error('video file ဒါမှမဟုတ် link တစ်ခု ထည့်ပေးပါ')
        st.stop()
    chain = st.checkbox('Generate ပြီးရင် Transform ပါ တွဲလုပ်', value=True,
                        key='chain')
    tmpd = tempfile.mkdtemp(prefix='mystudio_api_')
    try:
        if src_file:
            ext = os.path.splitext(src_file.name)[1] or '.mp4'
            src_path = os.path.join(tmpd, f'upload{ext}')
            with open(src_path, 'wb') as fh:
                fh.write(src_file.read())
        else:
            bar = st.progress(0)
            label = st.empty()

            def dcb(p):
                bar.progress(int(max(0.0, min(100.0, float(p)))))
                label.text(f'⬇️ Download {p:.0f}%')

            src_path = fetch_url_video(src_url, tmpd, progress_cb=dcb)
        label = st.empty()
        label.text('📡 API ကို ခေါ်နေတယ်…')
        gen_path = call_user_api(endpoint.strip(), api_key.strip(),
                                 src_path, prompt)
        out = os.path.join(tmpd, 'out.mp4')
        if chain:
            logo_path = None
            if logo_up:
                logo_path = os.path.join(
                    tmpd, 'logo' + os.path.splitext(logo_up.name)[1])
                with open(logo_path, 'wb') as fh:
                    fh.write(logo_up.read())
            bar = st.progress(0)
            label = st.empty()

            def tcb(p):
                bar.progress(int(max(0.0, min(100.0, float(p)))))
                label.text(f'⚙️ Transform {p:.0f}%')

            transform(gen_path, out, freeze_every=float(freeze_every),
                      freeze_dur=float(freeze_dur), do_flip=do_flip,
                      do_color=do_color, logo_path=logo_path,
                      logo_opacity=0.25, progress_cb=tcb)
        else:
            out = gen_path
        label.text('✅ ပြီးသွားပြီ!')
        with open(out, 'rb') as fh:
            st.download_button('⬇️ Video Download', fh.read(),
                               file_name='mystudio_api.mp4',
                               mime='video/mp4',
                               use_container_width=True)
        st.video(out)
    except Exception as e:  # noqa: BLE001
        st.error(f'အမှား: {e}')
