"""
🎬 Video Downloader Backend - Flask + yt-dlp + FFmpeg Support
Version: 3.0.0 - All Platforms Fixed + Instagram Quality Panel
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import shutil
import random
import requests
from urllib.parse import urlparse, parse_qs
from io import BytesIO

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# CORS Configuration
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300

# Multiple User-Agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
]

def get_ydl_opts(download_mode=False, format_id=None, prefer_merged=True, site='generic'):
    """Generate yt-dlp options with platform-specific bypass"""
    
    # Platform-specific options
    extractor_args = {}
    
    if site == 'youtube':
        extractor_args = {
            'youtube': {
                'player_client': 'web,ios',
                'player_skip': ['webpage'],
                'skip': ['dash', 'hls'],
                'lang': 'en'
            }
        }
    elif site == 'instagram':
        extractor_args = {
            'instagram': {
                'include_formats': True
            }
        }
    elif site == 'vimeo':
        extractor_args = {
            'vimeo': {}
        }
    
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False if not download_mode else 'in_playlist',
        'user_agent': random.choice(USER_AGENTS),
        'referer': 'https://www.google.com/',
        'socket_timeout': 30,
        'retries': 3,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        
        # Platform-specific extractor args
        'extractor_args': extractor_args,
        
        # Additional HTTP Headers to bypass bot detection
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        },
        
        # Cookie handling (if cookies.txt exists)
        'cookiefile': '/app/cookies.txt',
    }
    
    if download_mode:
        opts['format'] = format_id if format_id else 'best'
        if prefer_merged:
            opts['format'] = f"{format_id}/best[ext=mp4][vcodec!=none][acodec!=none]/best"
    
    return opts

def detect_site(url):
    """Detect which site the URL is from"""
    if 'youtube.com' in url or 'youtu.be' in url:
        return 'youtube'
    elif 'instagram.com' in url:
        return 'instagram'
    elif 'vimeo.com' in url:
        return 'vimeo'
    elif 'pinterest.com' in url:
        return 'pinterest'
    elif 'facebook.com' in url or 'fb.watch' in url:
        return 'facebook'
    elif 'tiktok.com' in url:
        return 'tiktok'
    else:
        return 'generic'

def sanitize_filename(filename):
    """Remove unsafe characters from filename"""
    if not filename:
        return 'video'
    filename = re.sub(r'[^\w\s\.\-]', '', filename)
    filename = re.sub(r'\s+', '_', filename.strip())
    return filename[:100]

def is_ffmpeg_available():
    """Check if FFmpeg is available on the system"""
    return shutil.which('ffmpeg') is not None

def format_instagram_qualities(formats):
    """Format Instagram qualities properly: 1080p, 720p, 480p, 360p, 240p, Audio"""
    qualities = []
    seen_labels = set()
    
    # Standard quality labels for Instagram
    quality_map = {
        1080: '1080p Full HD',
        720: '720p HD',
        480: '480p',
        360: '360p',
        240: '240p',
    }
    
    # First, collect video formats
    video_formats = []
    audio_formats = []
    
    for fmt in formats:
        if not fmt.get('format_id'):
            continue
            
        height = fmt.get('height', 0)
        vcodec = fmt.get('vcodec', 'none')
        acodec = fmt.get('acodec', 'none')
        ext = fmt.get('ext', 'mp4')
        filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
        
        # Skip if no video and no audio
        if vcodec == 'none' and acodec == 'none':
            continue
        
        # Audio only
        if vcodec == 'none' and acodec != 'none':
            audio_formats.append({
                'format_id': fmt['format_id'],
                'label': 'Audio Only',
                'ext': fmt.get('ext', 'm4a'),
                'filesize': filesize,
                'vcodec': '✗',
                'acodec': '✓',
                'url': fmt.get('url'),
            })
            continue
        
        # Video formats
        if height > 0:
            # Find the closest standard quality
            best_match = None
            for std_height, std_label in sorted(quality_map.items(), reverse=True):
                if height >= std_height:
                    best_match = (std_height, std_label)
                    break
            
            if best_match:
                label = best_match[1]
            else:
                label = f"{height}p"
            
            # Avoid duplicates
            if label in seen_labels:
                continue
            seen_labels.add(label)
            
            video_formats.append({
                'format_id': fmt['format_id'],
                'label': label,
                'ext': ext,
                'filesize': filesize,
                'vcodec': '✓',
                'acodec': '✓' if acodec != 'none' else '✗',
                'url': fmt.get('url'),
                'height': height,
            })
    
    # Sort video formats by height (highest first)
    video_formats.sort(key=lambda x: x.get('height', 0), reverse=True)
    
    # Add video formats
    qualities.extend(video_formats)
    
    # Add audio format (only one)
    if audio_formats:
        qualities.append(audio_formats[0])
    
    return qualities

def extract_video_info(url):
    """Extract video metadata and available formats"""
    site = detect_site(url)
    ydl_opts = get_ydl_opts(download_mode=False, site=site)
    ffmpeg_available = is_ffmpeg_available()
    
    logger.info(f"Starting extraction for: {url[:80]}... (Site: {site})")
    logger.info(f"FFmpeg available: {ffmpeg_available}")
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            logger.info("Calling ydl.extract_info()...")
            info = ydl.extract_info(url, download=False)
            
            if not info:
                logger.error("No info returned from yt-dlp")
                raise ValueError("No information returned from yt-dlp")
            
            logger.info(f"Extraction successful! Title: {info.get('title', 'Unknown')}")
            logger.info(f"Found {len(info.get('formats', []))} formats")
            
            # Format qualities based on site
            if site == 'instagram':
                qualities = format_instagram_qualities(info.get('formats', []))
            else:
                # Generic quality formatting for other sites
                qualities = []
                seen = set()
                
                for fmt in info.get('formats', []):
                    fmt_id = fmt.get('format_id')
                    if not fmt_id:
                        continue
                        
                    ext = fmt.get('ext', 'mp4')
                    height = fmt.get('height')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx')
                    vcodec = fmt.get('vcodec', 'none')
                    acodec = fmt.get('acodec', 'none')
                    fps = fmt.get('fps')
                    tbr = fmt.get('tbr')
                    protocol = fmt.get('protocol', 'https')
                    
                    # Skip incomplete formats
                    if vcodec == 'none' and acodec == 'none':
                        continue
                    
                    # FFmpeg-aware filtering
                    if not ffmpeg_available and vcodec != 'none' and acodec == 'none':
                        continue
                    if not ffmpeg_available and vcodec == 'none' and acodec != 'none':
                        continue
                    
                    # Create quality label
                    if height and height > 0:
                        label = f"{height}p"
                        if fps and fps > 30:
                            label += f"{int(fps)}"
                    elif fmt.get('resolution'):
                        label = fmt['resolution']
                    elif tbr:
                        label = f"{int(tbr)}k"
                    else:
                        label = fmt.get('format_note') or fmt_id
                    
                    # Avoid duplicates
                    key = f"{label}_{ext}_{vcodec}_{acodec}"
                    if key in seen:
                        continue
                    seen.add(key)
                    
                    # Format filesize
                    if filesize:
                        if filesize > 1024*1024*1024:
                            size_str = f"{filesize/1024/1024/1024:.1f} GB"
                        elif filesize > 1024*1024:
                            size_str = f"{filesize/1024/1024:.1f} MB"
                        else:
                            size_str = f"{filesize/1024:.1f} KB"
                    else:
                        size_str = "Unknown"
                    
                    # Check if format needs merging
                    needs_merge = (vcodec != 'none' and acodec == 'none') or (vcodec == 'none' and acodec != 'none')
                    
                    qualities.append({
                        'format_id': fmt_id,
                        'label': label,
                        'ext': ext,
                        'filesize': size_str,
                        'filesize_bytes': filesize,
                        'vcodec': '✓' if vcodec and vcodec != 'none' else '✗',
                        'acodec': '✓' if acodec and acodec != 'none' else '✗',
                        'fps': fps,
                        'protocol': protocol,
                        'url': fmt.get('url'),
                        'needs_ffmpeg': needs_merge and not ffmpeg_available,
                        'note': 'Requires FFmpeg' if needs_merge and not ffmpeg_available else None
                    })
                
                # Sort: prefer higher resolution + complete formats
                def quality_score(q):
                    res = int(''.join(filter(str.isdigit, str(q['label'])))) if any(c.isdigit() for c in str(q['label'])) else 0
                    complete = 0 if q.get('needs_ffmpeg', False) else 1
                    return (res * 100) + (complete * 50)
                
                qualities.sort(key=quality_score, reverse=True)
                qualities = qualities[:20]  # Limit to top 20
            
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
                'is_live': info.get('live_status') == 'is_live' or info.get('is_live', False)
            }
            
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e).lower()
            logger.error(f"Download error for {url}: {e}")
            
            # User-friendly error messages
            if 'private' in error_msg:
                raise ValueError("This video is private. Please use a public video.")
            elif 'unavailable' in error_msg or 'not found' in error_msg or 'does not exist' in error_msg:
                raise ValueError("Video not found or unavailable. Check the URL.")
            elif 'age' in error_msg or 'login' in error_msg or 'sign in' in error_msg or 'bot' in error_msg:
                raise ValueError("YouTube requires authentication. This may be a temporary block. Please try again later or use a different video.")
            elif 'region' in error_msg or 'blocked' in error_msg or 'not available in your country' in error_msg:
                raise ValueError("Video not available in this region.")
            elif 'live' in error_msg or 'streaming' in error_msg or 'upcoming' in error_msg:
                raise ValueError("Live streams cannot be downloaded. Wait until the stream ends.")
            elif 'copyright' in error_msg or 'removed' in error_msg:
                raise ValueError("Video removed due to copyright claim.")
            elif 'members-only' in error_msg or 'membership' in error_msg or 'join' in error_msg:
                raise ValueError("This video is for members only.")
            elif 'premium' in error_msg or 'paid' in error_msg:
                raise ValueError("This video requires YouTube Premium or purchase.")
            elif '403' in error_msg or 'forbidden' in error_msg:
                raise ValueError("Access forbidden. This site may be blocking automated requests. Try again later.")
            else:
                raise ValueError(f"Failed to extract video: {str(e)[:150]}")

@app.route('/')
def home():
    """Health check / info endpoint"""
    return jsonify({
        'service': 'Video Downloader API',
        'version': '3.0.0',
        'status': 'running',
        'ffmpeg': is_ffmpeg_available(),
        'endpoints': {
            'get_info': 'POST /api/get-info',
            'download': 'POST /api/download',
            'health': 'GET /health'
        }
    })

@app.route('/health')
def health_check():
    """Render.com health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'ffmpeg': is_ffmpeg_available(),
        'timestamp': time.time(),
        'version': '3.0.0'
    }), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
def get_video_info():
    """
    Extract video info and available qualities
    Request: {"url": "https://..."}
    Response: {success, title, thumbnail, qualities: [...], ffmpeg_available: bool}
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    start_time = time.time()
    
    try:
        data = request.get_json(silent=True)
        
        if not data:
            return jsonify({'success': False, 'error': 'Invalid JSON request'}), 400
        
        video_url = data.get('url', '').strip()
        
        if not video_url:
            return jsonify({'success': False, 'error': 'Video URL is required'}), 400
        
        # URL validation
        parsed = urlparse(video_url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'Invalid URL format. Must start with http:// or https://'}), 400
        
        # SSRF protection - block internal URLs
        blocked_domains = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.', '172.17.', '172.18.', '172.19.', '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.', '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.', 'file://', 'ftp://', 'gopher://']
        if any(blocked in video_url.lower() for blocked in blocked_domains):
            logger.warning(f"Blocked SSRF attempt: {video_url[:50]}...")
            return jsonify({'success': False, 'error': 'This URL is not allowed for security reasons'}), 403
        
        logger.info(f"Processing: {video_url[:50]}...")
        result = extract_video_info(video_url)
        
        elapsed = time.time() - start_time
        logger.info(f"✓ Extracted in {elapsed:.2f}s - {len(result['qualities'])} qualities found")
        
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        logger.error(f"yt-dlp error: {e}")
        
        if 'private' in error_msg or 'unavailable' in error_msg:
            return jsonify({'success': False, 'error': 'This video is private or unavailable'}), 403
        elif 'age' in error_msg or 'login' in error_msg or 'bot' in error_msg:
            return jsonify({'success': False, 'error': 'YouTube requires authentication. Try again later or use a different video.'}), 403
        elif 'region' in error_msg or 'blocked' in error_msg:
            return jsonify({'success': False, 'error': 'This video is not available in your region'}), 403
        elif 'live' in error_msg or 'streaming' in error_msg:
            return jsonify({'success': False, 'error': 'Live streams cannot be downloaded. Wait until it ends.'}), 400
        elif 'copyright' in error_msg:
            return jsonify({'success': False, 'error': 'Video removed due to copyright claim'}), 403
        elif '403' in error_msg or 'forbidden' in error_msg:
            return jsonify({'success': False, 'error': 'Access forbidden. This site may be blocking automated requests.'}), 403
        else:
            return jsonify({'success': False, 'error': f'Failed to fetch video: {str(e)[:150]}'}), 500
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:100]}'}), 500

@app.route('/api/download', methods=['POST', 'OPTIONS'])
def download_video():
    """
    Generate download link or proxy the video
    Request: {"url": "...", "format_id": "..."}
    Response: {success, download_url, title, filename}
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json(silent=True)
        
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        
        video_url = data.get('url', '').strip()
        format_id = data.get('format_id', 'best')
        
        if not video_url:
            return jsonify({'success': False, 'error': 'Video URL is required'}), 400
        
        logger.info(f"Download request: {video_url[:50]}... | format: {format_id}")
        
        site = detect_site(video_url)
        ydl_opts = get_ydl_opts(download_mode=True, format_id=format_id, site=site)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            if not info:
                return jsonify({'success': False, 'error': 'Failed to fetch video info'}), 500
            
            # Find the specific format
            direct_url = None
            selected_format = None
            
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    direct_url = fmt.get('url')
                    selected_format = fmt
                    break
            
            # If specific format not found, use the best URL
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
                return jsonify({
                    'success': False,
                    'error': 'Direct download not available for this format/platform.',
                    'hint': 'Try a different quality (720p or lower) or use the direct URL if available.',
                    'ffmpeg_note': '1080p+ formats may require FFmpeg merging.'
                }), 400
            
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        return jsonify({'success': False, 'error': f'Download failed: {str(e)[:150]}'}), 500
    except Exception as e:
        logger.exception(f"Download exception: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:100]}'}), 500

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed. Use POST for this endpoint.'}), 405

@app.errorhandler(429)
def rate_limit(e):
    return jsonify({'error': 'Too many requests. Please slow down.'}), 429

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting Video Downloader v3.0.0 on port {port}")
    logger.info(f"FFmpeg available: {is_ffmpeg_available()}")
    app.run(host='0.0.0.0', port=port, debug=False)
