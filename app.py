"""
🎬 Professional Video Downloader - No Cookies Required
Supports: YouTube, Instagram, TikTok, Facebook, Vimeo, Twitter, Pinterest
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
import shutil
import random
import requests
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
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

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per day", "20 per hour"]
)

app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024

# Invidious instances for YouTube (no cookies needed)
INVIDIOUS_INSTANCES = [
    'https://vid.puffyan.us',
    'https://invidious.flokinet.to',
    'https://yewtu.be',
    'https://inv.nadeko.net',
    'https://invidious.nerdvpn.de',
]

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
    elif 'pinterest.com' in url_lower or 'pin.it' in url_lower:
        return 'pinterest'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    else:
        return 'generic'

def extract_video_id(url):
    """Extract video ID from YouTube URL"""
    if 'v=' in url:
        return url.split('v=')[1].split('&')[0]
    elif 'youtu.be/' in url:
        return url.split('youtu.be/')[1].split('?')[0]
    elif 'youtube.com/shorts/' in url:
        return url.split('shorts/')[1].split('?')[0]
    return None

def extract_youtube_via_invidious(video_id):
    """Extract YouTube video info using Invidious API (no cookies)"""
    random.shuffle(INVIDIOUS_INSTANCES)
    
    for instance in INVIDIOUS_INSTANCES:
        try:
            logger.info(f"Trying Invidious: {instance}")
            api_url = f"{instance}/api/v1/videos/{video_id}"
            response = requests.get(api_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if response.status_code == 200:
                data = response.json()
                
                if 'error' in data:
                    logger.warning(f"Invidious error: {data['error']}")
                    continue
                
                qualities = []
                
                # Process adaptive formats (video only)
                if 'adaptiveFormats' in data:
                    video_formats = {}
                    
                    for fmt in data['adaptiveFormats']:
                        if fmt.get('type', '').startswith('video'):
                            height = fmt.get('height', 0)
                            if height >= 144:
                                # Find standard quality label
                                label_map = {
                                    2160: '2160p 4K',
                                    1440: '1440p 2K',
                                    1080: '1080p Full HD',
                                    720: '720p HD',
                                    480: '480p',
                                    360: '360p',
                                    240: '240p',
                                    144: '144p',
                                }
                                
                                label = None
                                for std_h, std_label in sorted(label_map.items(), reverse=True):
                                    if height >= std_h:
                                        label = std_label
                                        break
                                
                                if not label:
                                    label = f"{height}p"
                                
                                # Keep best quality for each label
                                if label not in video_formats or height > video_formats[label].get('height', 0):
                                    video_formats[label] = {
                                        'format_id': f"invidious_{fmt.get('itag', height)}",
                                        'label': label,
                                        'ext': 'mp4',
                                        'filesize': fmt.get('contentLength'),
                                        'vcodec': '✓',
                                        'acodec': '✗',
                                        'url': fmt.get('url'),
                                        'height': height,
                                        'fps': fmt.get('fps'),
                                    }
                    
                    # Add to qualities sorted by height
                    for label in sorted(video_formats.keys(), 
                                       key=lambda x: int(''.join(filter(str.isdigit, x)) or '0'), 
                                       reverse=True):
                        qualities.append(video_formats[label])
                
                # Add audio-only option
                for fmt in data.get('adaptiveFormats', []):
                    if fmt.get('type', '').startswith('audio'):
                        qualities.append({
                            'format_id': f"invidious_audio_{fmt.get('itag', '0')}",
                            'label': 'Audio Only',
                            'ext': 'm4a',
                            'filesize': fmt.get('contentLength'),
                            'vcodec': '✗',
                            'acodec': '✓',
                            'url': fmt.get('url'),
                            'height': 0,
                        })
                        break
                
                # Get thumbnail
                thumbnail = ''
                if data.get('videoThumbnails'):
                    thumbnail = data['videoThumbnails'][0].get('url', '')
                
                logger.info(f"✓ Invidious success! Found {len(qualities)} qualities")
                
                return {
                    'success': True,
                    'title': data.get('title', 'Unknown'),
                    'thumbnail': thumbnail,
                    'duration': data.get('lengthSeconds', 0),
                    'uploader': data.get('author', 'Unknown'),
                    'view_count': data.get('viewCount'),
                    'qualities': qualities,
                    'ffmpeg_available': False,
                    'platform': 'youtube',
                    'source': 'invidious',
                }
                
        except Exception as e:
            logger.warning(f"Invidious {instance} failed: {e}")
            continue
    
    return None

def get_ydl_opts(site='generic'):
    """Get yt-dlp options for non-YouTube sites"""
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
    
    if site == 'instagram':
        opts.update({
            'extractor_args': {'instagram': {'include_formats': True}},
            'http_headers': {'Accept': '*/*'}
        })
    elif site == 'vimeo':
        opts.update({
            'extractor_args': {'vimeo': {'force_noplaylist': True}},
            'http_headers': {'Referer': 'https://vimeo.com/'}
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
    elif site == 'pinterest':
        opts.update({
            'extractor_args': {'pinterest': {}},
            'http_headers': {'Referer': 'https://www.pinterest.com/'}
        })
    elif site == 'twitter':
        opts.update({
            'extractor_args': {'twitter': {}},
            'http_headers': {'Referer': 'https://twitter.com/'}
        })
    
    return opts

def format_qualities(formats):
    """Format qualities from yt-dlp"""
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
        fmt_url = fmt.get('url')
        
        if vcodec == 'none' and acodec == 'none':
            continue
        
        if vcodec == 'none' and acodec != 'none':
            audio_formats.append({
                'format_id': fmt_id, 'label': 'Audio Only', 'ext': ext,
                'filesize': filesize, 'vcodec': '✗', 'acodec': '✓',
                'url': fmt_url, 'height': 0,
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
                    'url': fmt_url, 'height': height, 'fps': fps,
                }
    
    sorted_video = sorted(video_formats.values(), key=lambda x: x.get('height', 0), reverse=True)
    qualities.extend(sorted_video)
    
    if audio_formats:
        qualities.append(audio_formats[0])
    
    return qualities

def extract_with_ytdlp(url, site, max_tries=2):
    """Extract using yt-dlp (for non-YouTube sites)"""
    for attempt in range(max_tries):
        try:
            opts = get_ydl_opts(site)
            logger.info(f"yt-dlp attempt {attempt + 1}/{max_tries} for {site}")
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise ValueError("No info returned")
                
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
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': False,
                    'platform': site,
                    'source': 'yt-dlp',
                }
                
        except Exception as e:
            logger.error(f"yt-dlp attempt {attempt + 1} failed: {e}")
            if attempt == max_tries - 1:
                raise
            time.sleep(2)

def extract_video_info(url):
    """Main extraction function"""
    site = detect_site(url)
    logger.info(f"Processing {url[:60]}... (Site: {site})")
    
    # YouTube: Use Invidious API (no cookies)
    if site == 'youtube':
        video_id = extract_video_id(url)
        if video_id:
            result = extract_youtube_via_invidious(video_id)
            if result:
                return result
        
        # Invidious failed, try yt-dlp as fallback
        try:
            return extract_with_ytdlp(url, 'youtube', max_tries=2)
        except Exception as e:
            raise ValueError(f"YouTube extraction failed: {str(e)[:100]}")
    
    # Other sites: Use yt-dlp
    else:
        return extract_with_ytdlp(url, site, max_tries=2)

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '11.0.0',
        'status': 'running',
        'cookies_required': False,
        'supported': ['YouTube', 'Instagram', 'TikTok', 'Facebook', 'Vimeo', 'Twitter', 'Pinterest'],
        'note': 'No cookies required! Uses Invidious API for YouTube.'
    })

@app.route('/health')
@limiter.limit("10 per minute")
def health():
    return jsonify({
        'status': 'healthy',
        'version': '11.0.0',
        'cookies': False
    }), 200

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
        if url == '':
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        parsed = urlparse(url)
        if parsed.scheme == '' or parsed.netloc == '':
            return jsonify({'success': False, 'error': 'Invalid URL'}), 400
        
        blocked = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.']
        if any(b in url.lower() for b in blocked):
            return jsonify({'success': False, 'error': 'URL not allowed'}), 403
        
        result = extract_video_info(url)
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
                title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))
                title = re.sub(r'\s+', '_', title.strip())[:100]
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
@limiter.limit("50 per hour per user")
def proxy_download():
    """Proxy download with attachment header"""
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
    logger.info(f"🚀 Starting Video Downloader v11.0.0 on port {port}")
    logger.info("✅ No cookies required - Using Invidious API for YouTube")
    app.run(host='0.0.0.0', port=port, debug=False)
