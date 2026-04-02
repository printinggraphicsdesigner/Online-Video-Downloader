"""
🎬 Video Downloader - All Sites Fixed
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import shutil
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
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

def detect_site(url):
    url_lower = url.lower()
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower or 'instagr.am' in url_lower:
        return 'instagram'
    elif 'vimeo.com' in url_lower:
        return 'vimeo'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower or 'fb.com' in url_lower:
        return 'facebook'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    else:
        return 'generic'

def get_ydl_opts(site='generic'):
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    ]
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 30,
        'user_agent': random.choice(user_agents),
        'geo_bypass': True,
        'extract_flat': False,
    }
    
    if site == 'youtube':
        opts.update({
            'extractor_args': {
                'youtube': {
                    'player_client': 'web,ios,android,mweb',
                    'player_skip': ['webpage'],
                    'skip': ['dash', 'hls'],
                }
            },
            'http_headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-Mode': 'navigate',
            }
        })
    elif site == 'instagram':
        opts.update({
            'extractor_args': {
                'instagram': {
                    'include_formats': True,
                    'embed_source': 'web'
                }
            },
            'http_headers': {
                'Accept': '*/*',
                'X-IG-App-ID': '936619743392459',
            }
        })
    elif site == 'vimeo':
        opts.update({
            'extractor_args': {
                'vimeo': {
                    'force_noplaylist': True,
                }
            },
            'http_headers': {
                'Referer': 'https://vimeo.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        })
    elif site == 'tiktok':
        opts.update({
            'extractor_args': {'tiktok': {}},
            'http_headers': {'User-Agent': 'Mozilla/5.0'}
        })
    elif site == 'facebook':
        opts.update({
            'extractor_args': {'facebook': {}},
            'http_headers': {'Referer': 'https://www.facebook.com/'}
        })
    elif site == 'twitter':
        opts.update({
            'extractor_args': {'twitter': {}},
            'http_headers': {'Referer': 'https://twitter.com/'}
        })
    
    return opts

def sanitize_filename(filename):
    if not filename:
        return 'video'
    filename = re.sub(r'[^\w\s\.\-]', '', filename)
    filename = re.sub(r'\s+', '_', filename.strip())
    return filename[:100]

def is_ffmpeg_available():
    return shutil.which('ffmpeg') is not None

def format_qualities(formats, site='generic'):
    qualities = []
    seen_labels = set()
    quality_labels = {
        2160: '2160p 4K', 1440: '1440p 2K', 1080: '1080p Full HD',
        720: '720p HD', 480: '480p', 360: '360p', 240: '240p', 144: '144p',
    }
    
    video_formats = {}
    audio_formats = []
    
    for fmt in formats:
        fmt_id = fmt.get('format_id')
        if not fmt_id:
            continue
        
        height = fmt.get('height', 0)
        vcodec = fmt.get('vcodec', 'none')
        acodec = fmt.get('acodec', 'none')
        ext = fmt.get('ext', 'mp4')
        filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        fps = fmt.get('fps')
        tbr = fmt.get('tbr', 0)
        fmt_url = fmt.get('url')
        protocol = fmt.get('protocol', 'https')
        
        if vcodec == 'none' and acodec == 'none':
            continue
        
        if vcodec == 'none' and acodec != 'none':
            audio_label = 'Audio Only'
            if tbr > 0:
                audio_label = f"Audio Only {int(tbr)}k"
            audio_formats.append({
                'format_id': fmt_id, 'label': audio_label, 'ext': ext,
                'filesize': filesize, 'vcodec': '✗', 'acodec': '✓',
                'url': fmt_url, 'protocol': protocol,
            })
            continue
        
        if height > 0:
            label = None
            for std_height, std_label in sorted(quality_labels.items(), reverse=True):
                if height >= std_height:
                    label = std_label
                    break
            if not label:
                label = f"{height}p"
            if fps and fps > 30 and fps not in [30, 60]:
                label += f" {int(fps)}fps"
            
            if label not in video_formats or height > video_formats[label].get('height', 0):
                video_formats[label] = {
                    'format_id': fmt_id, 'label': label, 'ext': ext,
                    'filesize': filesize, 'vcodec': '✓',
                    'acodec': '✓' if acodec != 'none' else '✗',
                    'url': fmt_url, 'height': height, 'fps': fps, 'protocol': protocol,
                }
        elif tbr > 0:
            label = f"{int(tbr)}k"
            if label not in seen_labels:
                seen_labels.add(label)
                video_formats[label] = {
                    'format_id': fmt_id, 'label': label, 'ext': ext,
                    'filesize': filesize, 'vcodec': '✓' if vcodec != 'none' else '✗',
                    'acodec': '✓' if acodec != 'none' else '✗',
                    'url': fmt_url, 'height': 0, 'fps': None, 'protocol': protocol,
                }
    
    sorted_video = sorted(video_formats.values(), key=lambda x: x.get('height', 0), reverse=True)
    qualities.extend(sorted_video)
    
    seen_audio = set()
    for audio in audio_formats:
        if audio['label'] not in seen_audio:
            seen_audio.add(audio['label'])
            qualities.append(audio)
    
    return qualities

def extract_with_retry(url, site, max_tries=4):
    for attempt in range(max_tries):
        try:
            opts = get_ydl_opts(site)
            
            if attempt == 1 and site == 'youtube':
                opts['extractor_args']['youtube']['player_client'] = 'web,ios,android,mweb'
            elif attempt >= 2:
                # For Vimeo, try different approach
                if site == 'vimeo':
                    opts = {
                        'quiet': True, 'no_warnings': True,
                        'no_check_certificate': True,
                        'noplaylist': True, 'ignoreerrors': False,
                        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'socket_timeout': 30,
                        'http_headers': {'Referer': 'https://vimeo.com/'},
                    }
                else:
                    opts = {
                        'quiet': True, 'no_warnings': True,
                        'no_check_certificate': True,
                        'noplaylist': True, 'ignoreerrors': False,
                        'user_agent': 'Mozilla/5.0',
                        'socket_timeout': 20,
                    }
            
            logger.info(f"Attempt {attempt + 1}/{max_tries} for {site}")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No info returned")
                
                formats = info.get('formats', [])
                if not formats and info.get('url'):
                    formats = [info]
                
                logger.info(f"Found {len(formats)} formats")
                qualities = format_qualities(formats, site)
                logger.info(f"Formatted {len(qualities)} qualities")
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Unknown'),
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': is_ffmpeg_available(),
                    'platform': site,
                }
                
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_tries - 1:
                error_lower = error_msg.lower()
                
                # Specific error handling
                if 'private' in error_lower or 'unavailable' in error_lower or 'does not exist' in error_lower:
                    raise ValueError("Video is private, unavailable, or does not exist")
                elif any(x in error_lower for x in ['bot', 'authentication', 'login', 'sign in', 'requires']):
                    raise ValueError("Site requires authentication. Try again later or use a different video.")
                elif 'region' in error_lower or 'blocked' in error_lower or 'not available in your country' in error_lower:
                    raise ValueError("Video not available in your region")
                elif 'live' in error_lower or 'streaming' in error_lower:
                    raise ValueError("Live stream cannot be downloaded")
                elif 'copyright' in error_lower or 'removed' in error_lower:
                    raise ValueError("Video removed due to copyright")
                elif '403' in error_lower or 'forbidden' in error_lower:
                    raise ValueError("Access forbidden by the site. Try again later.")
                elif 'config_url' in error_lower or 'keyerror' in error_lower:
                    if site == 'vimeo':
                        raise ValueError("Failed to extract Vimeo video. It may be private, password-protected, or restricted.")
                    else:
                        raise ValueError("Failed to extract video data. It may be private or restricted.")
                else:
                    raise ValueError(f"Failed to extract: {error_msg[:150]}")
            
            time.sleep(1.5 ** attempt)

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '9.0.0',
        'status': 'running',
        'supported': ['Instagram', 'TikTok', 'Facebook', 'Twitter', 'Vimeo (public)', 'YouTube (limited)'],
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'version': '9.0.0'}), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
def get_info():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json(silent=True)
        if data is None:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        
        url = data.get('url', '').strip()
        if url == '':
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        parsed = urlparse(url)
        if parsed.scheme == '' or parsed.netloc == '':
            return jsonify({'success': False, 'error': 'Invalid URL'}), 400
        
        blocked = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.']
        if any(b in url.lower() for b in blocked):
            return jsonify({'success': False, 'error': 'URL not allowed'}), 403
        
        site = detect_site(url)
        logger.info(f"Processing {url[:50]}... ({site})")
        
        result = extract_with_retry(url, site)
        return jsonify(result)
        
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error: {e}")
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
            if data is None:
                return jsonify({'success': False, 'error': 'Invalid request'}), 400
            url = data.get('url', '').strip()
            format_id = data.get('format_id', 'best')
        
        if url == '':
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
                title = sanitize_filename(info.get('title', 'video'))
                ext = 'mp4'
                return jsonify({
                    'success': True,
                    'download_url': download_url,
                    'title': info.get('title'),
                    'filename': f"{title}.{ext}",
                })
            else:
                raise ValueError("No URL found")
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'success': False, 'error': f'Failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-download')
def proxy_download():
    """Stream video with Content-Disposition: attachment header"""
    try:
        video_url = request.args.get('url')
        filename = request.args.get('filename', 'video.mp4')
        
        if not video_url:
            return jsonify({'error': 'URL required'}), 400
        
        logger.info(f"Proxy download: {video_url[:80]}...")
        
        response = requests.get(video_url, stream=True, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }, timeout=30)
        
        if response.status_code != 200:
            return jsonify({'error': f'Failed to fetch video: {response.status_code}'}), 500
        
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
        logger.error(f"Proxy download error: {e}")
        return jsonify({'error': f'Proxy failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-image')
def proxy_image():
    """Proxy for thumbnails to avoid CORS issues"""
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
        logger.error(f"Proxy image error: {e}")
        return jsonify({'error': f'Image proxy failed: {str(e)[:80]}'}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
