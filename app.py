"""
🎬 Video Downloader v15.0
YouTube cookies.txt পদ্ধতি — Render Environment Variable থেকে cookies নেয়
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp
import logging
import os
import re
import time
import subprocess
import tempfile
import random
import requests
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET","POST","OPTIONS"], "allow_headers": ["Content-Type"]}})
limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["200 per day","50 per hour"])
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

COOKIES_PATH = '/app/cookies/youtube.txt'

def ffmpeg_available():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

FFMPEG = ffmpeg_available()

def setup_cookies():
    """
    Render Environment Variable YOUTUBE_COOKIES থেকে cookies file বানাও।
    cookies.txt Netscape format এ থাকতে হবে।
    """
    cookies_env = os.environ.get('YOUTUBE_COOKIES', '')
    if not cookies_env:
        logger.warning("YOUTUBE_COOKIES env var not set — YouTube may fail")
        return False
    try:
        os.makedirs('/app/cookies', exist_ok=True)
        # Environment variable এ newline \n হিসেবে store হয়
        content = cookies_env.replace('\\n', '\n')
        with open(COOKIES_PATH, 'w') as f:
            f.write(content)
        logger.info(f"✅ Cookies written to {COOKIES_PATH} ({len(content)} chars)")
        return True
    except Exception as e:
        logger.error(f"Failed to write cookies: {e}")
        return False

COOKIES_OK = setup_cookies()
logger.info(f"FFmpeg={FFMPEG} | Cookies={'OK' if COOKIES_OK else 'MISSING'}")

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
]

QUALITY_LABELS = {
    2160: '2160p 4K', 1440: '1440p 2K', 1080: '1080p Full HD',
    720: '720p HD', 480: '480p', 360: '360p', 240: '240p', 144: '144p',
}

def detect_site(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u:     return 'youtube'
    if 'instagram.com' in u or 'instagr.am' in u: return 'instagram'
    if 'tiktok.com' in u:                          return 'tiktok'
    if 'facebook.com' in u or 'fb.watch' in u:     return 'facebook'
    if 'twitter.com' in u or 'x.com' in u:         return 'twitter'
    if 'vimeo.com' in u:                            return 'vimeo'
    if 'dailymotion.com' in u:                      return 'dailymotion'
    if 'pinterest.com' in u or 'pin.it' in u:       return 'pinterest'
    return 'generic'

def get_label(height, fps=None):
    label = f"{height}p"
    for std_h in sorted(QUALITY_LABELS.keys(), reverse=True):
        if height >= std_h:
            label = QUALITY_LABELS[std_h]
            break
    if fps and int(fps) > 30:
        label += f" {int(fps)}fps"
    return label

def format_qualities(formats, site='generic'):
    muxed = {}
    video_only = {}
    audio_only = []

    for fmt in formats:
        height  = fmt.get('height') or 0
        vcodec  = fmt.get('vcodec', 'none') or 'none'
        acodec  = fmt.get('acodec', 'none') or 'none'
        ext     = fmt.get('ext', 'mp4') or 'mp4'
        fsize   = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        fps     = fmt.get('fps')
        url     = fmt.get('url', '') or fmt.get('manifest_url', '')
        fmt_id  = fmt.get('format_id', '')
        proto   = fmt.get('protocol', '') or ''

        # HLS/DASH live stream formats — height 0 হলেও include করো
        is_live_fmt = proto in ('m3u8', 'm3u8_native', 'http_dash_segments', 'https_dash_segments')

        has_video = vcodec not in ('none', '', None)
        has_audio = acodec not in ('none', '', None)

        if not has_video and not has_audio:
            continue
        if not has_video and has_audio:
            audio_only.append({'format_id': fmt_id, 'label': 'Audio Only', 'ext': 'm4a',
                'filesize': fsize, 'vcodec': '✗', 'acodec': '✓', 'url': url, 'height': 0, 'needs_merge': False})
            continue

        # height 0 কিন্তু live stream — তবুও রাখো
        if height < 1 and not is_live_fmt:
            continue

        # Live stream এর জন্য height অনুমান করো resolution থেকে
        if height < 1:
            res = fmt.get('resolution', '') or ''
            if '1080' in res: height = 1080
            elif '720' in res: height = 720
            elif '480' in res: height = 480
            elif '360' in res: height = 360
            elif '240' in res: height = 240
            else: height = 360  # default

        label = get_label(height, fps)
        if is_live_fmt:
            label += ' 🔴'  # live indicator

        entry = {'format_id': fmt_id, 'label': label, 'ext': 'mp4',
                 'filesize': fsize, 'vcodec': '✓', 'height': height, 'fps': fps, 'url': url}

        if has_video and has_audio:
            entry.update({'acodec': '✓', 'needs_merge': False})
            if label not in muxed or height > muxed[label].get('height', 0):
                muxed[label] = entry
        elif has_video:
            entry.update({'acodec': '✗', 'needs_merge': True})
            if label not in video_only or height > video_only[label].get('height', 0):
                video_only[label] = entry

    qualities = []
    sort_key = lambda x: int(re.search(r'\d+', x).group() if re.search(r'\d+', x) else '0')

    for label in sorted(muxed.keys(), key=sort_key, reverse=True):
        qualities.append(muxed[label])
    if FFMPEG:
        for label in sorted(video_only.keys(), key=sort_key, reverse=True):
            if label not in muxed:
                vo = dict(video_only[label])
                vo['label'] += ' ⚡'
                qualities.append(vo)
    if not qualities and audio_only:
        qualities.append(audio_only[0])
    if not qualities:
        for label in sorted(video_only.keys(), key=sort_key, reverse=True):
            qualities.append(video_only[label])
    return qualities

def get_ydl_opts(site='generic'):
    ua = random.choice(USER_AGENTS)
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'retries': 3,
        'socket_timeout': 60,
        'user_agent': ua,
        'geo_bypass': True,
        # format নির্দিষ্ট করা নেই — সব format আনো, পরে আমরা filter করব
        'http_headers': {
            'User-Agent': ua,
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }

    # YouTube: cookies দিয়ে bot detection bypass
    if site == 'youtube' and COOKIES_OK and os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH
        logger.info("Using cookies.txt for YouTube")

    if site == 'instagram':
        opts['http_headers'].update({'X-IG-App-ID': '936619743392459', 'Referer': 'https://www.instagram.com/'})
    elif site == 'tiktok':
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'
    elif site == 'facebook':
        opts['http_headers']['Referer'] = 'https://www.facebook.com/'
    elif site == 'twitter':
        opts['http_headers']['Referer'] = 'https://twitter.com/'
    elif site == 'vimeo':
        opts['http_headers']['Referer'] = 'https://vimeo.com/'

    return opts

def extract_with_ytdlp(url, site, max_tries=3):
    last_error = None
    for attempt in range(max_tries):
        try:
            opts = get_ydl_opts(site)
            logger.info(f"[yt-dlp] attempt {attempt+1}/{max_tries} site={site}")
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No info returned")

                formats = info.get('formats', [])
                if not formats and info.get('url'):
                    formats = [info]

                qualities = format_qualities(formats, site)

                # যদি qualities খালি হয় — best format দিয়ে fallback
                if not qualities:
                    best_url = info.get('url') or info.get('manifest_url', '')
                    if best_url:
                        h = info.get('height', 0) or 0
                        label = get_label(h) if h else 'Best Quality'
                        qualities = [{'format_id': 'best', 'label': label,
                            'ext': info.get('ext','mp4'), 'filesize': 0,
                            'vcodec': '✓', 'acodec': '✓', 'url': best_url,
                            'height': h, 'needs_merge': False}]

                return {
                    'success': True,
                    'title': info.get('title','Unknown'),
                    'thumbnail': info.get('thumbnail',''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader') or info.get('channel','Unknown'),
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': FFMPEG,
                    'platform': site,
                    'source': 'yt-dlp',
                    'is_live': info.get('is_live', False),
                }
        except yt_dlp.utils.DownloadError as e:
            last_error = str(e)
            em = last_error.lower()
            logger.warning(f"[yt-dlp] attempt {attempt+1} failed: {last_error[:150]}")
            if any(k in em for k in ['private','unavailable','not exist','removed']):
                raise ValueError("ভিডিওটি প্রাইভেট বা সরিয়ে ফেলা হয়েছে।")
            if 'confirm you are not a bot' in em or ('sign in' in em and 'bot' in em):
                raise ValueError("YouTube cookies মেয়াদ শেষ। নতুন cookies দিন।")
            if 'requested format is not available' in em:
                # format problem — এটা retry করার দরকার নেই
                raise ValueError("এই ভিডিওর format পাওয়া যাচ্ছে না। অন্য ভিডিও চেষ্টা করুন।")
            if attempt < max_tries - 1:
                time.sleep(2 + attempt * 2)
        except Exception as e:
            last_error = str(e)
            if attempt < max_tries - 1:
                time.sleep(2)
    raise ValueError(f"ভিডিও তথ্য আনা সম্ভব হয়নি: {str(last_error)[:120]}")

def extract_video_info(url):
    site = detect_site(url)
    logger.info(f"Extracting: {url[:80]} | site={site}")
    return extract_with_ytdlp(url, site, max_tries=3)

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({'service': 'Video Downloader', 'version': '15.0.0',
                    'ffmpeg': FFMPEG, 'cookies': COOKIES_OK})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'ffmpeg': FFMPEG, 'cookies': COOKIES_OK}), 200

@app.route('/api/get-info', methods=['POST','OPTIONS'])
@limiter.limit("40 per hour")
def get_info():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'error': 'Invalid request'}), 400
    url = data.get('url','').strip()
    if not url:
        return jsonify({'success': False, 'error': 'URL required'}), 400
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({'success': False, 'error': 'Invalid URL'}), 400
    blocked = ['localhost','127.0.0.1','0.0.0.0','192.168.','10.','172.16.']
    if any(b in url.lower() for b in blocked):
        return jsonify({'success': False, 'error': 'URL not allowed'}), 403
    try:
        result = extract_video_info(url)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:80]}'}), 500

@app.route('/api/proxy-download')
@limiter.limit("60 per hour")
def proxy_download():
    video_url = request.args.get('url','').strip()
    filename  = request.args.get('filename','video.mp4')
    referer   = request.args.get('referer','')
    if not video_url:
        return jsonify({'error': 'URL required'}), 400
    ua = random.choice(USER_AGENTS)
    headers = {'User-Agent': ua, 'Accept': '*/*', 'Accept-Encoding': 'identity'}
    if referer:
        headers['Referer'] = referer
    range_h = request.headers.get('Range')
    if range_h:
        headers['Range'] = range_h
    try:
        resp = requests.get(video_url, stream=True, headers=headers, timeout=60, allow_redirects=True)
        if resp.status_code not in (200, 206):
            return jsonify({'error': f'Source returned {resp.status_code}'}), 502
        ct = resp.headers.get('Content-Type','video/mp4')
        cl = resp.headers.get('Content-Length')
        safe = re.sub(r'[^\w\s.\-]','',filename)[:120]
        rh = {'Content-Disposition': f'attachment; filename="{safe}"',
              'Content-Type': ct, 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-store'}
        if cl: rh['Content-Length'] = cl
        if resp.status_code == 206: rh['Content-Range'] = resp.headers.get('Content-Range','')
        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk: yield chunk
        return Response(stream_with_context(generate()), status=resp.status_code, headers=rh)
    except Exception as e:
        return jsonify({'error': f'Proxy failed: {str(e)[:80]}'}), 500

@app.route('/api/stream-download')
@limiter.limit("20 per hour")
def stream_download():
    if not FFMPEG:
        return jsonify({'error': 'FFmpeg not available'}), 503
    url      = request.args.get('url','').strip()
    quality  = request.args.get('quality','bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')
    filename = request.args.get('filename','video.mp4')
    if not url: return jsonify({'error': 'URL required'}), 400
    site = detect_site(url)
    tmp_dir = tempfile.mkdtemp(dir='/app/temp' if os.path.exists('/app/temp') else None)
    try:
        opts = get_ydl_opts(site)
        opts.update({'quiet': False, 'format': quality,
                     'outtmpl': os.path.join(tmp_dir,'video.%(ext)s'),
                     'merge_output_format': 'mp4'})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info: raise ValueError("Download failed")
        downloaded = next((os.path.join(tmp_dir,f) for f in os.listdir(tmp_dir) if f.startswith('video.')), None)
        if not downloaded or not os.path.exists(downloaded):
            raise ValueError("File not found after download")
        file_size = os.path.getsize(downloaded)
        safe = re.sub(r'[^\w\s.\-]','',filename)[:120]
        def generate():
            try:
                with open(downloaded,'rb') as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk: break
                        yield chunk
            finally:
                import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)
        return Response(stream_with_context(generate()), mimetype='video/mp4', headers={
            'Content-Disposition': f'attachment; filename="{safe}"',
            'Content-Length': str(file_size), 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error': f'Download failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-image')
def proxy_image():
    image_url = request.args.get('url','')
    if not image_url: return jsonify({'error': 'URL required'}), 400
    try:
        r = requests.get(image_url, headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=10)
        if r.status_code == 200:
            return Response(r.content, mimetype=r.headers.get('Content-Type','image/jpeg'),
                headers={'Cache-Control':'public, max-age=86400','Access-Control-Allow-Origin':'*'})
    except Exception as e:
        logger.error(f"Image proxy: {e}")
    return jsonify({'error': 'Image not available'}), 500

@app.errorhandler(404)
def not_found(e): return jsonify({'error': 'Not found'}), 404
@app.errorhandler(429)
def rate_limited(e): return jsonify({'error': 'অনেক বেশি request। একটু অপেক্ষা করুন।'}), 429
@app.errorhandler(500)
def server_err(e): return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 v15.0.0 port={port} FFmpeg={FFMPEG} Cookies={COOKIES_OK}")
    app.run(host='0.0.0.0', port=port, debug=False)
