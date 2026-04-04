"""
🎬 Professional Video Downloader - FULLY FIXED v13.0
Fixes:
  - YouTube: audio+video merged properly via yt-dlp (no Invidious dependency)
  - format_qualities: now includes merged formats correctly
  - TikTok/Facebook: improved headers & extractor args
  - proxy_download: proper headers forwarding to CDN
  - Invidious: kept as optional fallback only
  - Added /api/stream endpoint for server-side download+stream with ffmpeg merge
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

CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

# ─── Helpers ──────────────────────────────────────────────────────────────────

def detect_site(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u:   return 'youtube'
    if 'instagram.com' in u or 'instagr.am' in u: return 'instagram'
    if 'tiktok.com' in u:                          return 'tiktok'
    if 'facebook.com' in u or 'fb.watch' in u or 'fb.com' in u: return 'facebook'
    if 'twitter.com' in u or 'x.com' in u:        return 'twitter'
    if 'vimeo.com' in u:                           return 'vimeo'
    if 'dailymotion.com' in u:                     return 'dailymotion'
    if 'pinterest.com' in u or 'pin.it' in u:      return 'pinterest'
    return 'generic'

def ffmpeg_available():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

FFMPEG = ffmpeg_available()
logger.info(f"FFmpeg available: {FFMPEG}")

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
]

# ─── yt-dlp options per site ──────────────────────────────────────────────────

def get_ydl_opts(site='generic'):
    ua = random.choice(USER_AGENTS)

    base = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 60,
        'user_agent': ua,
        'geo_bypass': True,
        'http_headers': {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        },
    }

    # ── YouTube: bgutil POT provider + web client ──
    # bgutil server চলছে localhost:4416 তে (Dockerfile এ start হয়)
    # এটা YouTube এর BotGuard bypass করে PO Token generate করে
    if site == 'youtube':
        base.update({
            'extractor_args': {
                'youtube': {
                    'player_client': ['web'],
                },
                # bgutil HTTP server থেকে PO Token নেবে
                'youtubepot-bgutilhttp': {
                    'base_url': 'http://127.0.0.1:4416',
                },
            },
        })

    # ── Instagram ──
    elif site == 'instagram':
        base['http_headers'].update({
            'X-IG-App-ID': '936619743392459',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://www.instagram.com/',
        })

    # ── TikTok ──
    elif site == 'tiktok':
        base['http_headers'].update({
            'Referer': 'https://www.tiktok.com/',
            'Accept': 'application/json, text/plain, */*',
        })
        base['extractor_args'] = {
            'tiktok': {'webpage_download': True}
        }

    # ── Facebook ──
    elif site == 'facebook':
        base['http_headers'].update({
            'Referer': 'https://www.facebook.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'navigate',
        })

    # ── Twitter/X ──
    elif site == 'twitter':
        base['http_headers'].update({
            'Referer': 'https://twitter.com/',
            'Authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        })

    # ── Vimeo ──
    elif site == 'vimeo':
        base['http_headers']['Referer'] = 'https://vimeo.com/'

    # ── Dailymotion ──
    elif site == 'dailymotion':
        base['http_headers']['Referer'] = 'https://www.dailymotion.com/'

    # ── Pinterest ──
    elif site == 'pinterest':
        base['http_headers']['Referer'] = 'https://www.pinterest.com/'

    return base


# ─── Format quality list ───────────────────────────────────────────────────────

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
    """
    Build quality list from yt-dlp formats.
    Priority:
      1. Muxed (video+audio in one file) – always safe to download directly
      2. Video-only streams – need ffmpeg merge; flagged with needs_merge=True
      3. Audio-only – shown as fallback
    """
    muxed = {}       # label -> best muxed format
    video_only = {}  # label -> best video-only format
    audio_only = []

    for fmt in formats:
        fmt_id  = fmt.get('format_id', '')
        height  = fmt.get('height') or 0
        vcodec  = fmt.get('vcodec', 'none') or 'none'
        acodec  = fmt.get('acodec', 'none') or 'none'
        ext     = fmt.get('ext', 'mp4') or 'mp4'
        fsize   = fmt.get('filesize') or fmt.get('filesize_approx') or 0
        fps     = fmt.get('fps')
        url     = fmt.get('url', '')

        has_video = vcodec not in ('none', '', None)
        has_audio = acodec not in ('none', '', None)

        if not has_video and not has_audio:
            continue

        # Audio only
        if not has_video and has_audio:
            audio_only.append({
                'format_id': fmt_id, 'label': 'Audio Only (MP3)', 'ext': 'm4a',
                'filesize': fsize, 'vcodec': '✗', 'acodec': '✓',
                'url': url, 'height': 0, 'needs_merge': False,
            })
            continue

        if height < 1:
            continue

        label = get_label(height, fps)

        entry = {
            'format_id': fmt_id, 'label': label, 'ext': 'mp4',
            'filesize': fsize, 'vcodec': '✓', 'height': height, 'fps': fps,
            'url': url,
        }

        # Muxed (has both)
        if has_video and has_audio:
            entry['acodec'] = '✓'
            entry['needs_merge'] = False
            if label not in muxed or height > muxed[label].get('height', 0):
                muxed[label] = entry

        # Video-only (needs merge with ffmpeg)
        elif has_video and not has_audio:
            entry['acodec'] = '✗'
            entry['needs_merge'] = True
            if label not in video_only or height > video_only[label].get('height', 0):
                video_only[label] = entry

    qualities = []

    # Prefer muxed first
    for label in sorted(muxed.keys(), key=lambda x: int(re.search(r'\d+', x).group() if re.search(r'\d+', x) else '0'), reverse=True):
        qualities.append(muxed[label])

    # Add video-only if ffmpeg available (can merge on server)
    if FFMPEG:
        for label in sorted(video_only.keys(), key=lambda x: int(re.search(r'\d+', x).group() if re.search(r'\d+', x) else '0'), reverse=True):
            # Skip if muxed version already present
            if label not in muxed:
                vo = video_only[label]
                vo['label'] = vo['label'] + ' ⚡'  # mark as server-merged
                qualities.append(vo)

    # Fallback: add audio
    if audio_only:
        qualities.append(audio_only[0])

    # If nothing, return raw video-only without merge flag (let client handle)
    if not qualities:
        for label in sorted(video_only.keys(), key=lambda x: int(re.search(r'\d+', x).group() if re.search(r'\d+', x) else '0'), reverse=True):
            qualities.append(video_only[label])

    return qualities


# ─── Main extractor ────────────────────────────────────────────────────────────

def extract_with_ytdlp(url, site, max_tries=3):
    last_error = None
    for attempt in range(max_tries):
        try:
            opts = get_ydl_opts(site)
            logger.info(f"[yt-dlp] attempt {attempt+1}/{max_tries} site={site}")

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No info returned from yt-dlp")

                formats = info.get('formats', [])

                # Single-format video (e.g. direct mp4 link)
                if not formats and info.get('url'):
                    formats = [info]

                qualities = format_qualities(formats, site)

                # Last resort: if still empty, add raw url
                if not qualities and info.get('url'):
                    qualities = [{
                        'format_id': 'best',
                        'label': 'Best Quality',
                        'ext': info.get('ext', 'mp4'),
                        'filesize': 0,
                        'vcodec': '✓', 'acodec': '✓',
                        'url': info.get('url'),
                        'height': info.get('height', 0),
                        'needs_merge': False,
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
                    'source': 'yt-dlp',
                }

        except yt_dlp.utils.DownloadError as e:
            last_error = str(e)
            em = last_error.lower()
            logger.warning(f"[yt-dlp] attempt {attempt+1} failed: {last_error[:120]}")

            # Non-retriable errors
            if any(k in em for k in ['private', 'unavailable', 'not exist', 'removed']):
                raise ValueError("ভিডিওটি প্রাইভেট বা সরিয়ে ফেলা হয়েছে।")
            if any(k in em for k in ['sign in', 'log in', 'login required']):
                raise ValueError("এই ভিডিও ডাউনলোড করতে লগইন দরকার। কুকিজ ছাড়া সম্ভব নয়।")
            if 'confirm you are not a bot' in em or 'bot' in em:
                raise ValueError("সাইট বট হিসেবে ব্লক করছে। কিছুক্ষণ পর আবার চেষ্টা করুন।")

            if attempt < max_tries - 1:
                time.sleep(2 + attempt * 2)

        except Exception as e:
            last_error = str(e)
            logger.warning(f"[yt-dlp] unexpected error: {last_error[:120]}")
            if attempt < max_tries - 1:
                time.sleep(2)

    raise ValueError(f"ভিডিও তথ্য আনা সম্ভব হয়নি: {str(last_error)[:120]}")


def extract_video_info(url):
    site = detect_site(url)
    logger.info(f"Extracting: {url[:80]} | site={site}")
    return extract_with_ytdlp(url, site, max_tries=3)


# ─── Flask Routes ──────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '13.0.0',
        'status': 'running',
        'ffmpeg': FFMPEG,
        'supported': ['YouTube', 'Instagram', 'TikTok', 'Facebook', 'Vimeo', 'Twitter', 'Pinterest', 'Dailymotion'],
    })


@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'version': '13.0.0', 'ffmpeg': FFMPEG}), 200


@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
@limiter.limit("40 per hour")
def get_info():
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'success': False, 'error': 'Invalid request body'}), 400

    url = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'URL required'}), 400

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({'success': False, 'error': 'Invalid URL format'}), 400

    blocked = ['localhost', '127.0.0.1', '0.0.0.0', '192.168.', '10.', '172.16.']
    if any(b in url.lower() for b in blocked):
        return jsonify({'success': False, 'error': 'URL not allowed'}), 403

    try:
        result = extract_video_info(url)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'সার্ভার এরর: {str(e)[:80]}'}), 500


@app.route('/api/proxy-download')
@limiter.limit("60 per hour")
def proxy_download():
    """
    Stream a video URL through the server so the browser saves it as a file.
    Properly forwards headers needed for CDN auth (YouTube, TikTok, etc.)
    """
    video_url = request.args.get('url', '').strip()
    filename  = request.args.get('filename', 'video.mp4')
    referer   = request.args.get('referer', '')

    if not video_url:
        return jsonify({'error': 'URL required'}), 400

    # Basic SSRF protection
    try:
        p = urlparse(video_url)
        host = p.hostname or ''
        if host in ('localhost', '127.0.0.1', '0.0.0.0') or host.startswith('192.168.') or host.startswith('10.'):
            return jsonify({'error': 'URL not allowed'}), 403
    except Exception:
        return jsonify({'error': 'Bad URL'}), 400

    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept': '*/*',
        'Accept-Encoding': 'identity',  # no gzip so we can stream raw bytes
        'Connection': 'keep-alive',
    }
    if referer:
        headers['Referer'] = referer

    # Range support (needed for some CDNs)
    range_header = request.headers.get('Range')
    if range_header:
        headers['Range'] = range_header

    try:
        resp = requests.get(video_url, stream=True, headers=headers, timeout=60, allow_redirects=True)

        if resp.status_code not in (200, 206):
            logger.error(f"CDN returned {resp.status_code} for {video_url[:80]}")
            return jsonify({'error': f'Video source returned {resp.status_code}'}), 502

        content_type = resp.headers.get('Content-Type', 'video/mp4')
        content_length = resp.headers.get('Content-Length')

        safe_filename = re.sub(r'[^\w\s.\-]', '', filename)[:120]

        response_headers = {
            'Content-Disposition': f'attachment; filename="{safe_filename}"',
            'Content-Type': content_type,
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
        }
        if content_length:
            response_headers['Content-Length'] = content_length
        if resp.status_code == 206:
            response_headers['Content-Range'] = resp.headers.get('Content-Range', '')

        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            except Exception as e:
                logger.error(f"Stream error: {e}")

        status_code = resp.status_code
        return Response(
            stream_with_context(generate()),
            status=status_code,
            headers=response_headers,
        )

    except requests.exceptions.Timeout:
        return jsonify({'error': 'Video source timed out'}), 504
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        return jsonify({'error': f'Proxy failed: {str(e)[:80]}'}), 500


@app.route('/api/stream-download')
@limiter.limit("20 per hour")
def stream_download():
    """
    SERVER-SIDE DOWNLOAD: yt-dlp downloads the video on the server (with ffmpeg merge
    for video+audio) then streams it back to the browser.
    Use this for YouTube high-quality (1080p+) and TikTok/Twitter/Facebook.
    """
    if not FFMPEG:
        return jsonify({'error': 'FFmpeg not available on server'}), 503

    url      = request.args.get('url', '').strip()
    quality  = request.args.get('quality', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')
    filename = request.args.get('filename', 'video.mp4')

    if not url:
        return jsonify({'error': 'URL required'}), 400

    site = detect_site(url)
    tmp_dir = tempfile.mkdtemp(dir='/app/temp' if os.path.exists('/app/temp') else None)

    try:
        output_path = os.path.join(tmp_dir, 'video.%(ext)s')

        opts = get_ydl_opts(site)
        opts.update({
            'quiet': False,
            'format': quality,
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        })

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise ValueError("Download failed")

        # Find the downloaded file
        downloaded = None
        for f in os.listdir(tmp_dir):
            if f.startswith('video.'):
                downloaded = os.path.join(tmp_dir, f)
                break

        if not downloaded or not os.path.exists(downloaded):
            raise ValueError("Downloaded file not found")

        file_size = os.path.getsize(downloaded)
        safe_filename = re.sub(r'[^\w\s.\-]', '', filename)[:120]

        def generate():
            try:
                with open(downloaded, 'rb') as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                # Cleanup
                try:
                    import shutil
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

        return Response(
            stream_with_context(generate()),
            mimetype='video/mp4',
            headers={
                'Content-Disposition': f'attachment; filename="{safe_filename}"',
                'Content-Length': str(file_size),
                'Content-Type': 'video/mp4',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-store',
            }
        )

    except Exception as e:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.error(f"stream-download error: {e}", exc_info=True)
        return jsonify({'error': f'ডাউনলোড ব্যর্থ: {str(e)[:100]}'}), 500


@app.route('/api/proxy-image')
def proxy_image():
    image_url = request.args.get('url', '')
    if not image_url:
        return jsonify({'error': 'URL required'}), 400
    try:
        r = requests.get(image_url, headers={'User-Agent': random.choice(USER_AGENTS)}, timeout=10)
        if r.status_code == 200:
            ct = r.headers.get('Content-Type', 'image/jpeg')
            return Response(r.content, mimetype=ct, headers={
                'Cache-Control': 'public, max-age=86400',
                'Access-Control-Allow-Origin': '*',
            })
    except Exception as e:
        logger.error(f"Image proxy: {e}")
    return jsonify({'error': 'Image not available'}), 500


@app.errorhandler(404)
def not_found(e):    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(429)
def rate_limited(e): return jsonify({'error': 'অনেক বেশি request। একটু অপেক্ষা করুন।'}), 429

@app.errorhandler(500)
def server_err(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Server error'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Video Downloader v13.0.0 on port {port} | FFmpeg={FFMPEG}")
    app.run(host='0.0.0.0', port=port, debug=False)
