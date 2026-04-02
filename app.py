"""
🎬 Professional Video Downloader - Multi-Platform Support
WordPress Frontend + Render Backend (Docker)
Version: 5.0.0 - Production Ready
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import shutil
import random
import requests
from urllib.parse import urlparse, parse_qs, unquote

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

# Professional User-Agents
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
]

def detect_site(url):
    """Detect platform from URL"""
    url_lower = url.lower()
    if any(x in url_lower for x in ['youtube.com', 'youtu.be', 'youtube.com/shorts']):
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'vimeo.com' in url_lower:
        return 'vimeo'
    elif 'pinterest.com' in url_lower or 'pin.it' in url_lower:
        return 'pinterest'
    elif any(x in url_lower for x in ['facebook.com', 'fb.watch', 'fb.com']):
        return 'facebook'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    else:
        return 'generic'

def get_ydl_opts(site='generic', format_id=None, cookies_file=None):
    """Professional yt-dlp options"""
    
    # Platform-specific configurations
    platform_configs = {
        'youtube': {
            'extractor_args': {
                'youtube': {
                    'player_client': 'web,web_embedded,ios,android,mweb',
                    'player_skip': ['webpage'],
                    'lang': 'en',
                    'extractor_retries': 5,
                }
            },
            'http_headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
            }
        },
        'instagram': {
            'extractor_args': {'instagram': {'include_formats': True}},
            'http_headers': {
                'Accept': '*/*',
                'X-IG-App-ID': '936619743392459',
            }
        },
        'vimeo': {
            'extractor_args': {'vimeo': {'force_noplaylist': True}},
            'http_headers': {'Referer': 'https://vimeo.com/'}
        },
        'tiktok': {
            'extractor_args': {'tiktok': {'embed_source': 'embed'}},
            'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        },
        'twitter': {
            'extractor_args': {'twitter': {'api': 'graphql'}},
            'http_headers': {'Referer': 'https://twitter.com/'}
        },
        'facebook': {
            'extractor_args': {},
            'http_headers': {'Referer': 'https://www.facebook.com/'}
        },
        'pinterest': {
            'extractor_args': {},
            'http_headers': {'Referer': 'https://www.pinterest.com/'}
        }
    }
    
    config = platform_configs.get(site, platform_configs['generic'] if 'generic' in platform_configs else {
        'extractor_args': {},
        'http_headers': {}
    })
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'extractor_retries': 3,
        'fragment_retries': 3,
        'retries': 5,
        'socket_timeout': 60,
        'user_agent': random.choice(USER_AGENTS),
        'referer': 'https://www.google.com/',
        'extractor_args': config.get('extractor_args', {}),
        'http_headers': config.get('http_headers', {}),
        'geo_bypass': True,
        'geo_bypass_country': 'US',
    }
    
    # Add cookies if available
    if cookies_file and os.path.exists(cookies_file):
        opts['cookiefile'] = cookies_file
    
    if format_id:
        opts['format'] = format_id
    else:
        opts['format'] = 'best[ext=mp4]/best'
    
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
    """Format qualities for all platforms"""
    qualities = []
    seen = set()
    
    # Standard quality labels
    quality_map = {
        2160: '2160p 4K',
        1440: '1440p 2K',
        1080: '1080p Full HD',
        720: '720p HD',
        480: '480p',
        360: '360p',
        240: '240p',
        144: '144p',
    }
    
    video_formats = []
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
        
        # Skip incomplete formats
        if vcodec == 'none' and acodec == 'none':
            continue
        
        # Audio only
        if vcodec == 'none' and acodec != 'none':
            audio_formats.append({
                'format_id': fmt_id,
                'label': 'Audio Only',
                'ext': fmt.get('ext', 'm4a'),
                'filesize': filesize,
                'vcodec': '✗',
                'acodec': '✓',
                'url': fmt_url,
            })
            continue
        
        # Video formats
        if height > 0:
            # Find closest standard quality
            label = None
            for std_height, std_label in sorted(quality_map.items(), reverse=True):
                if height >= std_height:
                    label = std_label
                    break
            if not label:
                label = f"{height}p"
            
            if fps and fps > 30:
                label += f" {int(fps)}fps"
            
            if label in seen:
                continue
            seen.add(label)
            
            video_formats.append({
                'format_id': fmt_id,
                'label': label,
                'ext': ext,
                'filesize': filesize,
                'vcodec': '✓',
                'acodec': '✓' if acodec != 'none' else '✗',
                'url': fmt_url,
                'height': height,
                'fps': fps,
            })
        elif tbr > 0:
            # For formats without height (audio-only or unknown)
            label = f"{int(tbr)}k"
            if label not in seen:
                seen.add(label)
                video_formats.append({
                    'format_id': fmt_id,
                    'label': label,
                    'ext': ext,
                    'filesize': filesize,
                    'vcodec': '✓' if vcodec != 'none' else '✗',
                    'acodec': '✓' if acodec != 'none' else '✗',
                    'url': fmt_url,
                    'height': 0,
                    'fps': None,
                })
    
    # Sort by height (highest first)
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    qualities.extend(video_formats)
    
    # Add audio format
    if audio_formats:
        qualities.append(audio_formats[0])
    
    return qualities

def extract_video_info(url, max_attempts=3):
    """Extract video info with multiple attempts and fallbacks"""
    site = detect_site(url)
    ffmpeg_available = is_ffmpeg_available()
    cookies_file = '/app/cookies.txt' if os.path.exists('/app/cookies.txt') else None
    
    logger.info(f"Extracting from {url[:80]}... (Site: {site})")
    
    for attempt in range(max_attempts):
        try:
            # Try with different configurations
            if attempt == 0:
                # First attempt: Standard extraction
                ydl_opts = get_ydl_opts(site=site, cookies_file=cookies_file)
            elif attempt == 1:
                # Second attempt: With different player clients (for YouTube)
                if site == 'youtube':
                    ydl_opts = get_ydl_opts(site=site, cookies_file=cookies_file)
                    ydl_opts['extractor_args']['youtube']['player_client'] = 'web,ios,android,mweb'
                else:
                    ydl_opts = get_ydl_opts(site=site, cookies_file=cookies_file)
            else:
                # Third attempt: Minimal options
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'no_check_certificate': True,
                    'noplaylist': True,
                    'ignoreerrors': False,
                    'user_agent': random.choice(USER_AGENTS),
                    'socket_timeout': 30,
                    'geo_bypass': True,
                }
                if cookies_file:
                    ydl_opts['cookiefile'] = cookies_file
            
            logger.info(f"Attempt {attempt + 1}/{max_attempts}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise ValueError("No information returned")
                
                logger.info(f"✓ Success! Title: {info.get('title', 'Unknown')}")
                
                # Format qualities
                formats = info.get('formats', [])
                if not formats and info.get('url'):
                    # Single format video
                    formats = [info]
                
                qualities = format_qualities(formats, site)
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown Title'),
                    'thumbnail': info.get('thumbnail') or (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader') or info.get('channel') or 'Unknown',
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': ffmpeg_available,
                    'webpage_url': info.get('webpage_url', url),
                    'is_live': info.get('live_status') == 'is_live' or info.get('is_live', False),
                    'platform': site,
                }
                
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_attempts - 1:
                # Final attempt failed - provide user-friendly error
                if any(x in error_msg for x in ['private', 'unavailable', 'not found', 'does not exist']):
                    raise ValueError("Video not found or unavailable. Check the URL.")
                elif any(x in error_msg for x in ['age', 'login', 'sign in', 'bot']):
                    raise ValueError("YouTube requires authentication. Try again later or use a different video.")
                elif any(x in error_msg for x in ['region', 'blocked', 'not available in your country']):
                    raise ValueError("Video not available in your region.")
                elif any(x in error_msg for x in ['live', 'streaming', 'upcoming']):
                    raise ValueError("Live streams cannot be downloaded. Wait until it ends.")
                elif any(x in error_msg for x in ['copyright', 'removed']):
                    raise ValueError("Video removed due to copyright claim.")
                elif any(x in error_msg for x in ['members-only', 'membership', 'join']):
                    raise ValueError("This video is for members only.")
                elif any(x in error_msg for x in ['403', 'forbidden']):
                    raise ValueError("Access forbidden. This site may be blocking automated requests.")
                elif 'config_url' in error_msg or 'keyerror' in error_msg:
                    raise ValueError("Failed to extract video data. This video may be private or restricted.")
                elif 'drm' in error_msg or 'protected' in error_msg:
                    raise ValueError("This video is DRM protected and cannot be downloaded.")
                else:
                    raise ValueError(f"Failed to extract video: {str(e)[:150]}")
            
            time.sleep(2 ** attempt)

@app.route('/')
def home():
    return jsonify({
        'service': 'Professional Video Downloader API',
        'version': '5.0.0',
        'status': 'running',
        'ffmpeg': is_ffmpeg_available(),
        'supported_sites': ['YouTube', 'Instagram', 'Vimeo', 'Pinterest', 'Facebook', 'TikTok', 'Twitter'],
        'endpoints': {
            'get_info': 'POST /api/get-info',
            'download': 'GET or POST /api/download',
            'health': 'GET /health'
        }
    })

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'ffmpeg': is_ffmpeg_available(),
        'timestamp': time.time(),
        'version': '5.0.0'
    }), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
def get_video_info():
    if request.method == 'OPTIONS':
        return '', 204
    
    start_time = time.time()
    
    try:
        data = request.get_json(silent=True)
        if not 
            return jsonify({'success': False, 'error': 'Invalid JSON request'}), 400
        
        video_url = data.get('url', '').strip()
        if not video_url:
            return jsonify({'success': False, 'error': 'Video URL is required'}), 400
        
        parsed = urlparse(video_url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'Invalid URL format. Must start with http:// or https://'}), 400
        
        # SSRF protection
        blocked = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.', 'file://', 'ftp://', 'gopher://']
        if any(b in video_url.lower() for b in blocked):
            return jsonify({'success': False, 'error': 'This URL is not allowed for security reasons'}), 403
        
        logger.info(f"Processing: {video_url[:50]}...")
        result = extract_video_info(video_url)
        
        elapsed = time.time() - start_time
        logger.info(f"✓ Extracted in {elapsed:.2f}s - {len(result['qualities'])} qualities")
        
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:100]}'}), 500

@app.route('/api/download', methods=['GET', 'POST', 'OPTIONS'])
def download_video():
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        if request.method == 'GET':
            video_url = request.args.get('url', '').strip()
            format_id = request.args.get('format_id', 'best')
        else:
            data = request.get_json(silent=True)
            if not 
                return jsonify({'success': False, 'error': 'Invalid request'}), 400
            video_url = data.get('url', '').strip()
            format_id = data.get('format_id', 'best')
        
        if not video_url:
            return jsonify({'success': False, 'error': 'Video URL is required'}), 400
        
        logger.info(f"Download: {video_url[:50]}... | format: {format_id}")
        
        site = detect_site(video_url)
        cookies_file = '/app/cookies.txt' if os.path.exists('/app/cookies.txt') else None
        
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                ydl_opts = get_ydl_opts(site=site, format_id=format_id, cookies_file=cookies_file)
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        raise ValueError("Failed to fetch video info")
                    
                    # Find specific format
                    direct_url = None
                    selected_format = None
                    
                    for fmt in info.get('formats', []):
                        if fmt.get('format_id') == format_id:
                            direct_url = fmt.get('url')
                            selected_format = fmt
                            break
                    
                    if not direct_url:
                        direct_url = info.get('url')
                        selected_format = info
                    
                    if direct_url:
                        title = sanitize_filename(info.get('title', 'video'))
                        ext = selected_format.get('ext', 'mp4') if selected_format else 'mp4'
                        filesize = selected_format.get('filesize') if selected_format else info.get('filesize')
                        
                        return jsonify({
                            'success': True,
                            'method': 'direct',
                            'download_url': direct_url,
                            'title': info.get('title'),
                            'filename': f"{title}.{ext}",
                            'ext': ext,
                            'filesize': filesize,
                            'hint': 'If download fails, try right-click → "Save link as..." or use a download manager.'
                        })
                    else:
                        raise ValueError("No download URL found")
                        
            except Exception as e:
                logger.error(f"Download attempt {attempt + 1} failed: {e}")
                if attempt == max_attempts - 1:
                    raise
                time.sleep(2 ** attempt)
            
    except Exception as e:
        logger.error(f"Download error: {e}")
        return jsonify({'success': False, 'error': f'Download failed: {str(e)[:150]}'}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed. Use GET or POST.'}), 405

@app.errorhandler(429)
def rate_limit(e):
    return jsonify({'error': 'Too many requests. Please slow down.'}), 429

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting Professional Video Downloader v5.0.0 on port {port}")
    logger.info(f"FFmpeg available: {is_ffmpeg_available()}")
    app.run(host='0.0.0.0', port=port, debug=False)
