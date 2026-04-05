"""
🎬 Video Downloader v16.0 - Final Fix
- CORS সব সময় কাজ করবে
- YouTube cookies দিয়ে কাজ করবে
- Live stream, Shorts, Normal video সব handle করবে
- Format error gracefully handle করবে
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

# ── CORS — সব origin থেকে allow করো ──────────────────────────────────────────
CORS(app, resources={r"/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization", "X-API-Key", "x-api-key"],
    "expose_headers": ["Content-Disposition", "Content-Length"],
}})

# CORS preflight এর জন্য extra handler
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-API-Key, x-api-key'
    return response

limiter = Limiter(app=app, key_func=get_remote_address,
                  default_limits=["500 per day", "100 per hour"])
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
    cookies_env = os.environ.get('YOUTUBE_COOKIES', '')
    if not cookies_env:
        logger.warning("YOUTUBE_COOKIES not set")
        return False
    try:
        os.makedirs('/app/cookies', exist_ok=True)
        content = cookies_env.replace('\\n', '\n')
        with open(COOKIES_PATH, 'w') as f:
            f.write(content)
        logger.info(f"Cookies written: {len(content)} chars")
        return True
    except Exception as e:
        logger.error(f"Cookie write failed: {e}")
        return False

COOKIES_OK = setup_cookies()
logger.info(f"FFmpeg={FFMPEG} | Cookies={'OK' if COOKIES_OK else 'MISSING'}")

# ── Security: API Key + Domain Protection ─────────────────────────────────────
# Render Environment এ সেট করুন:
#   API_SECRET_KEY = যেকোনো random string (যেমন: mysite_2026_xyz)
#   ALLOWED_ORIGINS = আপনার domain (যেমন: easylifez.com)
API_SECRET_KEY    = os.environ.get('API_SECRET_KEY', '')
ALLOWED_ORIGINS_ENV = os.environ.get('ALLOWED_ORIGINS', '')

ALLOWED_ORIGINS_LIST = [o.strip().lower() for o in ALLOWED_ORIGINS_ENV.split(',') if o.strip()]
logger.info(f"API Key set: {bool(API_SECRET_KEY)} | Allowed: {ALLOWED_ORIGINS_LIST}")

def is_authorized(req):
    # কিছুই set না থাকলে সবার জন্য open (setup না হওয়া পর্যন্ত)
    if not API_SECRET_KEY and not ALLOWED_ORIGINS_LIST:
        return True
    # API Key দিয়ে authorize
    if API_SECRET_KEY:
        key = req.headers.get('X-API-Key', '') or req.args.get('api_key', '')
        if key == API_SECRET_KEY:
            return True
    # Domain দিয়ে authorize (Referer বা Origin header)
    if ALLOWED_ORIGINS_LIST:
        origin  = (req.headers.get('Origin', '') or '').lower()
        referer = (req.headers.get('Referer', '') or '').lower()
        for allowed in ALLOWED_ORIGINS_LIST:
            if allowed in origin or allowed in referer:
                return True
    return False

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

def format_qualities(formats, is_live=False):
    muxed = {}
    video_only = {}
    audio_only = []
    sort_key = lambda x: int(re.search(r'\d+', x).group() if re.search(r'\d+', x) else '0')

    for fmt in formats:
        height  = fmt.get('height') or 0
        vcodec  = fmt.get('vcodec', 'none') or 'none'
        acodec  = fmt.get('acodec', 'none') or 'none'
        ext     = fmt.get('ext', 'mp4') or 'mp4'
        fsize   = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        fps     = fmt.get('fps')
        url     = fmt.get('url') or fmt.get('manifest_url', '')
        fmt_id  = fmt.get('format_id', '')
        proto   = fmt.get('protocol', '') or ''
        res     = fmt.get('resolution', '') or ''

        is_stream = proto in ('m3u8', 'm3u8_native', 'http_dash_segments')
        has_video = vcodec not in ('none', '', None)
        has_audio = acodec not in ('none', '', None)

        if not has_video and not has_audio:
            continue

        # Audio only
        if not has_video and has_audio:
            audio_only.append({
                'format_id': fmt_id, 'label': 'Audio Only', 'ext': 'm4a',
                'filesize': fsize, 'vcodec': '✗', 'acodec': '✓',
                'url': url, 'height': 0, 'needs_merge': False,
            })
            continue

        # Height বের করো
        if height < 1:
            for marker in ['2160','1440','1080','720','480','360','240','144']:
                if marker in res or marker in fmt_id:
                    height = int(marker)
                    break
            if height < 1:
                if is_stream:
                    height = 360  # live stream default
                else:
                    continue  # height নেই, skip

        label = get_label(height, fps)
        if is_live or is_stream:
            label = label + ' 🔴'

        entry = {
            'format_id': fmt_id, 'label': label, 'ext': 'mp4',
            'filesize': fsize, 'vcodec': '✓', 'height': height,
            'fps': fps, 'url': url,
        }

        if has_video and has_audio:
            entry.update({'acodec': '✓', 'needs_merge': False})
            if label not in muxed or height > muxed[label].get('height', 0):
                muxed[label] = entry
        elif has_video:
            entry.update({'acodec': '✗', 'needs_merge': True})
            if label not in video_only or height > video_only[label].get('height', 0):
                video_only[label] = entry

    qualities = []
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
        'http_headers': {
            'User-Agent': ua,
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }
    if site == 'youtube' and COOKIES_OK and os.path.exists(COOKIES_PATH):
        opts['cookiefile'] = COOKIES_PATH
        logger.info("Using YouTube cookies")
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

                is_live = info.get('is_live', False) or '/live/' in url
                formats = info.get('formats', [])

                # DEBUG LOG — কতগুলো format আসছে দেখো
                logger.info(f"[DEBUG] formats count={len(formats)} is_live={is_live}")
                for i, f in enumerate(formats[:8]):
                    logger.info(f"[DEBUG] fmt[{i}] id={f.get('format_id')} "
                               f"h={f.get('height')} vc={f.get('vcodec','?')[:8]} "
                               f"ac={f.get('acodec','?')[:8]} ext={f.get('ext','?')} "
                               f"proto={f.get('protocol','?')}")

                if not formats and (info.get('url') or info.get('manifest_url')):
                    formats = [info]

                qualities = format_qualities(formats, is_live=is_live)
                logger.info(f"[DEBUG] qualities after filter={len(qualities)}")

                # Fallback: যদি কোনো quality না পাওয়া যায়
                if not qualities:
                    best_url = info.get('url') or info.get('manifest_url', '')
                    if best_url:
                        h = info.get('height', 0) or 360
                        qualities = [{
                            'format_id': 'best', 'label': get_label(h),
                            'ext': info.get('ext', 'mp4'), 'filesize': 0,
                            'vcodec': '✓', 'acodec': '✓', 'url': best_url,
                            'height': h, 'needs_merge': False,
                        }]

                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader') or info.get('channel', 'Unknown'),
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': FFMPEG,
                    'platform': site,
                    'is_live': is_live,
                    'source': 'yt-dlp',
                }

        except yt_dlp.utils.DownloadError as e:
            last_error = str(e)
            em = last_error.lower()
            logger.warning(f"[yt-dlp] attempt {attempt+1} failed: {last_error[:150]}")

            if any(k in em for k in ['private', 'unavailable', 'not exist', 'removed']):
                raise ValueError("ভিডিওটি প্রাইভেট বা মুছে ফেলা হয়েছে।")
            if 'not a bot' in em or ('sign in' in em and 'confirm' in em):
                raise ValueError("YouTube cookies মেয়াদ শেষ হয়েছে। নতুন cookies দিন।")
            if 'requested format is not available' in em:
                raise ValueError("এই ভিডিওর format পাওয়া যাচ্ছে না। ভিন্ন ভিডিও চেষ্টা করুন।")
            if 'live event will begin' in em:
                raise ValueError("এই ভিডিওটি এখনো শুরু হয়নি।")

            if attempt < max_tries - 1:
                time.sleep(2 + attempt)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[yt-dlp] unexpected: {last_error[:100]}")
            if attempt < max_tries - 1:
                time.sleep(2)

    raise ValueError(f"ভিডিও তথ্য আনা সম্ভব হয়নি: {str(last_error)[:120]}")

def extract_video_info(url):
    site = detect_site(url)
    logger.info(f"Extracting: {url[:80]} | site={site}")
    return extract_with_ytdlp(url, site, max_tries=3)

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def home():
    return jsonify({'service': 'Video Downloader', 'version': '16.0.0',
                    'status': 'running', 'ffmpeg': FFMPEG, 'cookies': COOKIES_OK})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'version': '16.0.0',
                    'ffmpeg': FFMPEG, 'cookies': COOKIES_OK}), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS', 'GET'])
@limiter.limit("60 per hour")
def get_info():
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
        return resp

    # Authorization check
    if not is_authorized(request):
        logger.warning(f"Unauthorized request from: {request.headers.get('Origin','?')} | {request.remote_addr}")
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'success': False, 'error': 'URL দিন'}), 400

    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'সঠিক URL দিন'}), 400
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid URL'}), 400

    blocked = ['localhost', '127.0.0.1', '0.0.0.0', '192.168.', '10.', '172.16.']
    if any(b in url.lower() for b in blocked):
        return jsonify({'success': False, 'error': 'URL allowed না'}), 403

    try:
        result = extract_video_info(url)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:80]}'}), 500

@app.route('/api/proxy-download', methods=['GET', 'OPTIONS'])
@limiter.limit("100 per hour")
def proxy_download():
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    video_url = request.args.get('url', '').strip()
    filename  = request.args.get('filename', 'video.mp4')
    referer   = request.args.get('referer', '')

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

        ct = resp.headers.get('Content-Type', 'video/mp4')
        cl = resp.headers.get('Content-Length')
        safe = re.sub(r'[^\w\s.\-]', '', filename)[:120]

        rh = {
            'Content-Disposition': f'attachment; filename="{safe}"',
            'Content-Type': ct,
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
        }
        if cl: rh['Content-Length'] = cl
        if resp.status_code == 206:
            rh['Content-Range'] = resp.headers.get('Content-Range', '')

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk: yield chunk

        return Response(stream_with_context(generate()), status=resp.status_code, headers=rh)
    except Exception as e:
        return jsonify({'error': f'Proxy failed: {str(e)[:80]}'}), 500

@app.route('/api/stream-download', methods=['GET', 'OPTIONS'])
@limiter.limit("20 per hour")
def stream_download():
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp

    if not FFMPEG:
        return jsonify({'error': 'FFmpeg not available'}), 503

    url      = request.args.get('url', '').strip()
    quality  = request.args.get('quality', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')
    filename = request.args.get('filename', 'video.mp4')

    if not url:
        return jsonify({'error': 'URL required'}), 400

    site = detect_site(url)
    tmp_dir = tempfile.mkdtemp(dir='/app/temp' if os.path.exists('/app/temp') else None)

    try:
        opts = get_ydl_opts(site)
        opts.update({
            'quiet': False,
            'format': quality,
            'outtmpl': os.path.join(tmp_dir, 'video.%(ext)s'),
            'merge_output_format': 'mp4',
        })

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise ValueError("Download failed")

        downloaded = next(
            (os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.startswith('video.')), None)
        if not downloaded or not os.path.exists(downloaded):
            raise ValueError("File not found after download")

        file_size = os.path.getsize(downloaded)
        safe = re.sub(r'[^\w\s.\-]', '', filename)[:120]

        def generate():
            try:
                with open(downloaded, 'rb') as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk: break
                        yield chunk
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        return Response(stream_with_context(generate()), mimetype='video/mp4', headers={
            'Content-Disposition': f'attachment; filename="{safe}"',
            'Content-Length': str(file_size),
            'Access-Control-Allow-Origin': '*',
        })

    except Exception as e:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error': f'Download failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-image', methods=['GET'])
def proxy_image():
    image_url = request.args.get('url', '')
    if not image_url:
        return jsonify({'error': 'URL required'}), 400
    try:
        r = requests.get(image_url, headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=10)
        if r.status_code == 200:
            return Response(r.content, mimetype=r.headers.get('Content-Type', 'image/jpeg'),
                headers={'Cache-Control': 'public, max-age=86400', 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        logger.error(f"Image proxy: {e}")
    return jsonify({'error': 'Image not available'}), 500

@app.errorhandler(404)
def not_found(e): return jsonify({'error': 'Not found'}), 404

@app.errorhandler(429)
def rate_limited(e): return jsonify({'error': 'অনেক request। একটু অপেক্ষা করুন।'}), 429

@app.errorhandler(500)
def server_err(e):
    logger.error(f"500 error: {e}")
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 v16.0.0 port={port} FFmpeg={FFMPEG} Cookies={COOKIES_OK}")
    app.run(host='0.0.0.0', port=port, debug=False)
