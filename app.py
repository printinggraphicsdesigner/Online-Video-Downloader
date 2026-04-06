"""
Video Downloader v1.0
YouTube বাদে সব সাইট সাপোর্ট করে।
Instagram, TikTok, Facebook, Twitter, Vimeo, Dailymotion ইত্যাদি।
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp
import logging
import os
import re
import requests
import random
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

CORS(app, resources={r"/api/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type"],
}})

@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

limiter = Limiter(app=app, key_func=get_remote_address,
                  default_limits=["200 per day", "50 per hour"])

app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
]

QUALITY_LABELS = {
    2160: '2160p 4K',
    1440: '1440p 2K',
    1080: '1080p Full HD',
    720: '720p HD',
    480: '480p',
    360: '360p',
    240: '240p',
    144: '144p',
}


def detect_site(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u:
        return 'youtube'
    if 'instagram.com' in u or 'instagr.am' in u:
        return 'instagram'
    if 'tiktok.com' in u:
        return 'tiktok'
    if 'facebook.com' in u or 'fb.watch' in u or 'fb.com' in u:
        return 'facebook'
    if 'twitter.com' in u or 'x.com' in u:
        return 'twitter'
    if 'vimeo.com' in u:
        return 'vimeo'
    if 'dailymotion.com' in u:
        return 'dailymotion'
    if 'pinterest.com' in u or 'pin.it' in u:
        return 'pinterest'
    return 'generic'


def get_ydl_opts(site):
    ua = random.choice(USER_AGENTS)

    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 3,
        'socket_timeout': 30,
        'user_agent': ua,
        'http_headers': {
            'User-Agent': ua,
            'Accept-Language': 'en-US,en;q=0.9',
        },
    }

    if site == 'instagram':
        opts['http_headers'].update({
            'X-IG-App-ID': '936619743392459',
            'Referer': 'https://www.instagram.com/',
        })
    elif site == 'tiktok':
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'
    elif site == 'facebook':
        opts['http_headers']['Referer'] = 'https://www.facebook.com/'
    elif site == 'twitter':
        opts['http_headers']['Referer'] = 'https://twitter.com/'
    elif site == 'vimeo':
        opts['http_headers']['Referer'] = 'https://vimeo.com/'
    elif site == 'dailymotion':
        opts['http_headers']['Referer'] = 'https://www.dailymotion.com/'
    elif site == 'pinterest':
        opts['http_headers']['Referer'] = 'https://www.pinterest.com/'

    return opts


def get_quality_label(height, fps=None):
    label = f"{height}p"
    for std_h in sorted(QUALITY_LABELS.keys(), reverse=True):
        if height >= std_h:
            label = QUALITY_LABELS[std_h]
            break
    if fps and int(fps) > 30:
        label += f" {int(fps)}fps"
    return label


def build_qualities(formats):
    muxed = {}    # video + audio একসাথে
    video_only = {}
    audio_only = []

    for fmt in formats:
        fmt_id  = fmt.get('format_id', '')
        height  = fmt.get('height') or 0
        vcodec  = fmt.get('vcodec') or 'none'
        acodec  = fmt.get('acodec') or 'none'
        fsize   = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        fps     = fmt.get('fps')
        url     = fmt.get('url', '')
        ext     = fmt.get('ext', 'mp4')

        has_v = vcodec != 'none'
        has_a = acodec != 'none'

        if not has_v and not has_a:
            continue

        # Audio only
        if not has_v and has_a:
            audio_only.append({
                'format_id': fmt_id, 'label': 'Audio Only',
                'ext': ext, 'filesize': fsize,
                'vcodec': '✗', 'acodec': '✓',
                'url': url, 'height': 0,
            })
            continue

        if height < 1:
            continue

        label = get_quality_label(height, fps)

        # Muxed (video + audio)
        if has_v and has_a:
            entry = {
                'format_id': fmt_id, 'label': label, 'ext': 'mp4',
                'filesize': fsize, 'vcodec': '✓', 'acodec': '✓',
                'url': url, 'height': height,
            }
            if label not in muxed or height > muxed[label]['height']:
                muxed[label] = entry

        # Video only
        elif has_v:
            entry = {
                'format_id': fmt_id, 'label': label + ' (no audio)', 'ext': 'mp4',
                'filesize': fsize, 'vcodec': '✓', 'acodec': '✗',
                'url': url, 'height': height,
            }
            if label not in video_only or height > video_only[label]['height']:
                video_only[label] = entry

    # Sort by height descending
    def sort_key(x):
        m = re.search(r'\d+', x)
        return int(m.group()) if m else 0

    qualities = []
    for label in sorted(muxed.keys(), key=sort_key, reverse=True):
        qualities.append(muxed[label])

    # Video only গুলো শুধু তখনই add করব যদি muxed এ সেই quality না থাকে
    for label in sorted(video_only.keys(), key=sort_key, reverse=True):
        base_label = label.replace(' (no audio)', '')
        if base_label not in muxed:
            qualities.append(video_only[label])

    # Audio only সবার শেষে
    if audio_only:
        qualities.append(audio_only[0])

    return qualities


def extract_info(url, site):
    opts = get_ydl_opts(site)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError("ভিডিও তথ্য পাওয়া যায়নি")

        formats = info.get('formats', [])
        if not formats and info.get('url'):
            formats = [info]

        qualities = build_qualities(formats)

        # Fallback
        if not qualities and info.get('url'):
            h = info.get('height', 0) or 0
            qualities = [{
                'format_id': 'best',
                'label': get_quality_label(h) if h else 'Best Quality',
                'ext': info.get('ext', 'mp4'),
                'filesize': 0,
                'vcodec': '✓', 'acodec': '✓',
                'url': info['url'],
                'height': h,
            }]

        return {
            'success': True,
            'title': info.get('title', 'Unknown'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader') or info.get('channel', ''),
            'view_count': info.get('view_count'),
            'qualities': qualities,
            'platform': site,
        }


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({'status': 'running', 'version': '1.0'})


@app.route('/health')
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
@limiter.limit("60 per hour")
def get_info():
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'success': False, 'error': 'URL দিন'}), 400

    # URL validate
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'সঠিক URL দিন'}), 400
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid URL'}), 400

    site = detect_site(url)

    # YouTube block করা
    if site == 'youtube':
        return jsonify({
            'success': False,
            'error': 'YouTube ভিডিও এই সাইটে সাপোর্ট করে না। আমাদের Desktop App ব্যবহার করুন।'
        }), 400

    try:
        result = extract_info(url, site)
        return jsonify(result)

    except yt_dlp.utils.DownloadError as e:
        err = str(e).lower()
        logger.warning(f"DownloadError [{site}]: {str(e)[:150]}")

        if 'private' in err or 'unavailable' in err:
            msg = 'ভিডিওটি প্রাইভেট বা মুছে ফেলা হয়েছে।'
        elif 'login' in err or 'sign in' in err:
            msg = 'এই ভিডিও দেখতে লগইন দরকার।'
        elif 'not found' in err or '404' in err:
            msg = 'ভিডিওটি খুঁজে পাওয়া যায়নি।'
        else:
            msg = f'ভিডিও তথ্য আনতে সমস্যা হয়েছে। আবার চেষ্টা করুন।'

        return jsonify({'success': False, 'error': msg}), 400

    except Exception as e:
        logger.error(f"Error [{site}]: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'সার্ভার সমস্যা। আবার চেষ্টা করুন।'}), 500


@app.route('/api/proxy-download')
@limiter.limit("100 per hour")
def proxy_download():
    video_url = request.args.get('url', '').strip()
    filename  = request.args.get('filename', 'video.mp4')
    referer   = request.args.get('referer', '')

    if not video_url:
        return jsonify({'error': 'URL required'}), 400

    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept': '*/*',
        'Accept-Encoding': 'identity',
    }
    if referer:
        headers['Referer'] = referer

    range_header = request.headers.get('Range')
    if range_header:
        headers['Range'] = range_header

    try:
        resp = requests.get(video_url, stream=True, headers=headers,
                           timeout=60, allow_redirects=True)

        if resp.status_code not in (200, 206):
            return jsonify({'error': f'Source error: {resp.status_code}'}), 502

        ct = resp.headers.get('Content-Type', 'video/mp4')
        cl = resp.headers.get('Content-Length')
        safe_name = re.sub(r'[^\w\s.\-]', '', filename)[:120]

        resp_headers = {
            'Content-Disposition': f'attachment; filename="{safe_name}"',
            'Content-Type': ct,
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
        }
        if cl:
            resp_headers['Content-Length'] = cl
        if resp.status_code == 206:
            resp_headers['Content-Range'] = resp.headers.get('Content-Range', '')

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(stream_with_context(generate()),
                       status=resp.status_code, headers=resp_headers)

    except Exception as e:
        logger.error(f"Proxy error: {e}")
        return jsonify({'error': 'Download failed'}), 500


@app.route('/api/proxy-image')
def proxy_image():
    image_url = request.args.get('url', '')
    if not image_url:
        return jsonify({'error': 'URL required'}), 400
    try:
        r = requests.get(image_url,
                        headers={'User-Agent': random.choice(USER_AGENTS)},
                        timeout=10)
        if r.status_code == 200:
            return Response(r.content,
                          mimetype=r.headers.get('Content-Type', 'image/jpeg'),
                          headers={
                              'Cache-Control': 'public, max-age=86400',
                              'Access-Control-Allow-Origin': '*',
                          })
    except Exception as e:
        logger.error(f"Image proxy: {e}")
    return jsonify({'error': 'Image not available'}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({'error': 'অনেক বেশি request। একটু অপেক্ষা করুন।'}), 429

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
