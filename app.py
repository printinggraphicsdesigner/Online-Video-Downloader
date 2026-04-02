"""
🎬 Video Downloader - Fixed: No Warnings + No Hanging
Best effort without cookies
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.storage import MemoryStorage
import yt_dlp
import logging
import os
import re
import time
import random
import requests
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Enable CORS
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Rate limiting with explicit memory storage (fixes warning)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per day", "20 per hour"],
    storage_uri="memory://",  # ✅ Explicit storage - fixes warning
    strategy="fixed-window"
)

app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

def detect_site(url):
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    elif 'pinterest.com' in url_lower:
        return 'pinterest'
    elif 'vimeo.com' in url_lower:
        return 'vimeo'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
        return 'facebook'
    elif 'dailymotion.com' in url_lower:
        return 'dailymotion'
    else:
        return 'generic'

def get_ydl_opts(site='generic'):
    """Optimized options for each platform"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 30,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'geo_bypass': True,
        'extract_flat': False,
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    
    if site == 'instagram':
        opts.update({
            'extractor_args': {'instagram': {'include_formats': True}},
            'http_headers': {'X-IG-App-ID': '936619743392459'}
        })
    elif site == 'tiktok':
        opts.update({
            'extractor_args': {'tiktok': {'embed_source': 'embed'}},
            'http_headers': {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'}
        })
    elif site == 'twitter':
        opts.update({
            'extractor_args': {'twitter': {}},
            'http_headers': {'Referer': 'https://twitter.com/'}
        })
    elif site == 'pinterest':
        opts.update({
            'extractor_args': {'pinterest': {}},
            'http_headers': {'Referer': 'https://www.pinterest.com/'}
        })
    elif site == 'vimeo':
        opts.update({
            'extractor_args': {'vimeo': {'force_noplaylist': True}},
            'http_headers': {'Referer': 'https://vimeo.com/'}
        })
    
    return opts

def format_qualities(formats):
    """Extract only working qualities (video + audio)"""
    qualities = []
    seen = set()
    
    for fmt in formats:
        fmt_id = fmt.get('format_id')
        if not fmt_id:
            continue
        
        height = fmt.get('height', 0)
        vcodec = fmt.get('vcodec', 'none')
        acodec = fmt.get('acodec', 'none')
        ext = fmt.get('ext', 'mp4')
        filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        url = fmt.get('url')
        
        # Only include if has both video and audio (avoid corrupt files)
        if vcodec != 'none' and acodec != 'none' and url:
            label = f"{height}p" if height > 0 else "Unknown"
            if height == 1080:
                label = "1080p Full HD"
            elif height == 720:
                label = "720p HD"
            
            if label not in seen:
                seen.add(label)
                qualities.append({
                    'format_id': fmt_id,
                    'label': label,
                    'ext': ext,
                    'filesize': filesize,
                    'vcodec': '✓',
                    'acodec': '✓',
                    'url': url,
                    'height': height,
                })
    
    # Sort by height
    qualities.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    # Add audio-only if no video found
    if not qualities:
        for fmt in formats:
            if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none':
                qualities.append({
                    'format_id': fmt.get('format_id'),
                    'label': 'Audio Only',
                    'ext': fmt.get('ext', 'm4a'),
                    'filesize': fmt.get('filesize'),
                    'vcodec': '✗',
                    'acodec': '✓',
                    'url': fmt.get('url'),
                    'height': 0,
                })
                break
    
    return qualities

def extract_with_ytdlp(url, site, max_tries=2):
    """Extract with better error handling and timeout"""
    for attempt in range(max_tries):
        try:
            opts = get_ydl_opts(site)
            logger.info(f"Attempt {attempt + 1} for {site}")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No info")
                
                formats = info.get('formats', [])
                if not formats and info.get('url'):
                    formats = [info]
                
                qualities = format_qualities(formats)
                
                if not qualities:
                    raise ValueError("No downloadable qualities found")
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown'),
                    'qualities': qualities,
                    'ffmpeg_available': False,
                    'platform': site,
                }
                
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_tries - 1:
                if 'private' in error_msg or 'unavailable' in error_msg:
                    raise ValueError("Video is private or unavailable")
                elif 'bot' in error_msg or 'authentication' in error_msg or 'sign in' in error_msg:
                    raise ValueError(f"{site.title()} is blocking automated requests. Try again later.")
                elif 'region' in error_msg or 'blocked' in error_msg:
                    raise ValueError("Video not available in your region")
                elif '403' in error_msg or 'forbidden' in error_msg:
                    raise ValueError("Access forbidden. The site is blocking requests.")
                elif 'redirect' in error_msg or '302' in error_msg:
                    raise ValueError("Redirect detected. Video may be private.")
                else:
                    raise ValueError(f"Extraction failed: {str(e)[:100]}")
            
            time.sleep(2)

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '14.0.0',
        'status': 'running',
        'best_supported': ['Instagram', 'TikTok', 'Twitter', 'Pinterest'],
        'limited': ['YouTube (some videos)', 'Vimeo (public only)'],
        'note': 'Some sites block automated requests from datacenter IPs'
    })

@app.route('/health')
@limiter.limit("10 per minute")
def health():
    return jsonify({'status': 'healthy', 'version': '14.0.0'}), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per hour per user")
def get_info():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        
        url = data.get('url', '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'Invalid URL'}), 400
        
        # Security check
        blocked = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.']
        if any(b in url.lower() for b in blocked):
            return jsonify({'success': False, 'error': 'URL not allowed'}), 403
        
        site = detect_site(url)
        logger.info(f"Processing {url[:50]}... ({site})")
        
        result = extract_with_ytdlp(url, site, max_tries=2)
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"Extraction error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Server error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:80]}'}), 500

@app.route('/api/download', methods=['GET', 'POST', 'OPTIONS'])
@limiter.limit("50 per hour per user")
def download():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        if request.method == 'GET':
            url = request.args.get('url', '').strip()
            format_id = request.args.get('format_id', 'best')
        else:
            data = request.get_json(silent=True)
            if data is None:
                return jsonify({'success': False, 'error': 'Invalid request'}), 400
            url = data.get('url', '').strip()
            format_id = data.get('format_id', 'best')
        
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        site = detect_site(url)
        opts = get_ydl_opts(site)
        opts['format'] = format_id
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("No info")
            
            download_url = None
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    download_url = fmt.get('url')
                    break
            
            if not download_url:
                download_url = info.get('url')
            
            if download_url:
                title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))[:100]
                return jsonify({
                    'success': True,
                    'download_url': download_url,
                    'title': info.get('title'),
                    'filename': f"{title}.mp4",
                })
            else:
                raise ValueError("No URL found")
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'success': False, 'error': f'Failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-download')
@limiter.limit("50 per hour per user")
def proxy_download():
    """Proxy download with proper streaming and timeout"""
    try:
        video_url = request.args.get('url')
        filename = request.args.get('filename', 'video.mp4')
        
        if not video_url:
            return jsonify({'error': 'URL required'}), 400
        
        logger.info(f"Proxy download: {video_url[:80]}...")
        
        # Stream with proper timeout and headers
        response = requests.get(
            video_url,
            stream=True,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*',
            },
            timeout=60  # ✅ Longer timeout to prevent hanging
        )
        
        if response.status_code != 200:
            return jsonify({'error': f'Failed: {response.status_code}'}), 500
        
        content_type = response.headers.get('Content-Type', 'video/mp4')
        content_length = response.headers.get('Content-Length')
        
        def generate():
            try:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            except Exception as e:
                logger.error(f"Streaming error: {e}")
        
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': content_type,
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
            'X-Accel-Buffering': 'no',  # ✅ Disable Nginx buffering (Render uses Nginx)
        }
        
        if content_length:
            headers['Content-Length'] = content_length
        
        return Response(
            stream_with_context(generate()),
            mimetype=content_type,
            headers=headers
        )
        
    except requests.exceptions.Timeout:
        logger.error("Proxy download timeout")
        return jsonify({'error': 'Download timeout. Try again or use a smaller video.'}), 504
    except requests.exceptions.ConnectionError:
        logger.error("Proxy download connection error")
        return jsonify({'error': 'Connection failed. The video URL may be expired.'}), 502
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        return jsonify({'error': f'Proxy failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-image')
def proxy_image():
    """Proxy for thumbnails"""
    try:
        image_url = request.args.get('url')
        if not image_url:
            return jsonify({'error': 'URL required'}), 400
        
        response = requests.get(image_url, headers={
            'User-Agent': 'Mozilla/5.0',
        }, timeout=10)
        
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            return Response(
                response.content,
                mimetype=content_type,
                headers={
                    'Cache-Control': 'public, max-age=86400',
                    'Access-Control-Allow-Origin': '*',
                }
            )
        else:
            return jsonify({'error': 'Failed to fetch image'}), 500
            
    except Exception as e:
        logger.error(f"Image proxy error: {e}")
        return jsonify({'error': f'Image failed: {str(e)[:80]}'}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(429)
def rate_limit(e):
    return jsonify({'error': 'Too many requests. Please wait a few minutes.'}), 429

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting Video Downloader v14.0.0 on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
