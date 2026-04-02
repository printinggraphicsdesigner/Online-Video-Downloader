"""
🎬 Video Downloader - Production Ready
No warnings, no errors
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import requests
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Enable CORS
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"]}})

app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

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
    else:
        return 'generic'

def get_ydl_opts(site='generic'):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 3,
        'socket_timeout': 30,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'geo_bypass': True,
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    
    if site == 'instagram':
        opts['http_headers']['X-IG-App-ID'] = '936619743392459'
    elif site == 'tiktok':
        opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'
    elif site in ['twitter', 'pinterest', 'vimeo']:
        opts['http_headers']['Referer'] = f'https://{site}.com/'
    
    return opts

def format_qualities(formats):
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
    
    qualities.sort(key=lambda x: x.get('height', 0), reverse=True)
    
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

def extract_video(url, site):
    try:
        opts = get_ydl_opts(site)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None, "No info returned"
            
            formats = info.get('formats', [])
            if not formats and info.get('url'):
                formats = [info]
            
            qualities = format_qualities(formats)
            
            return {
                'success': True,
                'title': info.get('title', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'qualities': qualities,
                'platform': site,
            }, None
            
    except Exception as e:
        logger.error(f"Extraction error: {e}")
        return None, str(e)

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '15.0.0',
        'status': 'running',
        'supported': ['Instagram', 'TikTok', 'Twitter', 'Pinterest', 'Vimeo', 'YouTube'],
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'version': '15.0.0'}), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
def get_info():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json(silent=True)
        if not data:
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
        
        result, error = extract_video(url, site)
        
        if error:
            error_lower = error.lower()
            if 'private' in error_lower or 'unavailable' in error_lower:
                return jsonify({'success': False, 'error': 'Video is private or unavailable'}), 400
            elif 'bot' in error_lower or 'authentication' in error_lower:
                return jsonify({'success': False, 'error': f'{site.title()} is blocking requests. Try again later.'}), 400
            elif 'region' in error_lower or 'blocked' in error_lower:
                return jsonify({'success': False, 'error': 'Video not available in your region'}), 400
            else:
                return jsonify({'success': False, 'error': f'Failed: {error[:100]}'}), 500
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Server error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:80]}'}), 500

@app.route('/api/download', methods=['GET', 'POST', 'OPTIONS'])
def download():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        if request.method == 'GET':
            url = request.args.get('url', '').strip()
            format_id = request.args.get('format_id', 'best')
        else:
            data = request.get_json(silent=True)
            if not data:
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
def proxy_download():
    try:
        video_url = request.args.get('url')
        filename = request.args.get('filename', 'video.mp4')
        
        if not video_url:
            return jsonify({'error': 'URL required'}), 400
        
        logger.info(f"Proxy download: {video_url[:80]}...")
        
        response = requests.get(video_url, stream=True, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }, timeout=60)
        
        if response.status_code != 200:
            return jsonify({'error': f'Failed: {response.status_code}'}), 500
        
        content_type = response.headers.get('Content-Type', 'video/mp4')
        
        def generate():
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        return Response(
            stream_with_context(generate()),
            mimetype=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': content_type,
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*',
            }
        )
        
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        return jsonify({'error': f'Proxy failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-image')
def proxy_image():
    try:
        image_url = request.args.get('url')
        if not image_url:
            return jsonify({'error': 'URL required'}), 400
        
        response = requests.get(image_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        
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

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting Video Downloader v15.0.0 on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
