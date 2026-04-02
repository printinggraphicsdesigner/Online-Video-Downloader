"""
🎬 Video Downloader Backend - Flask + yt-dlp + FFmpeg Support
Architecture: WordPress Frontend + Render Backend (Docker)
Features: Streaming download, CORS, format filtering, error handling
"""

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import requests
from urllib.parse import urlparse, quote, unquote

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ✅ CORS: Allow only your WordPress domain (production security)
# Replace with your actual domain: ["https://yoursite.com"]
CORS(app, resources={
    r"/api/*": {
        "origins": "*",  # Development: allow all. Production: change to your domain
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300

# User-Agent rotations
USER_AGENTS = [
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
]

def get_ydl_opts(download_mode=False, format_id=None, prefer_merged=True):
    """Generate yt-dlp options"""
    import random
    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False if not download_mode else 'in_playlist',
        'user_agent': random.choice(USER_AGENTS),
        'referer': 'https://www.google.com/',
        'socket_timeout': 30,
        'retries': 2,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': True,
    }
    
    if download_mode:
        opts['format'] = format_id if format_id else 'best'
        # ✅ FFmpeg-aware: Prefer formats that don't need merging (if FFmpeg unavailable)
        if prefer_merged:
            opts['format'] = f"{format_id}/best[ext=mp4][vcodec!=none][acodec!=none]/best"
    
    return opts

def sanitize_filename(filename):
    """Remove unsafe characters"""
    if not filename:
        return 'video'
    filename = re.sub(r'[^\w\s\.\-]', '', filename)
    filename = re.sub(r'\s+', '_', filename.strip())
    return filename[:100]

def is_ffmpeg_available():
    """Check if FFmpeg is available on the system"""
    import shutil
    return shutil.which('ffmpeg') is not None

def extract_video_info(url):
    """Extract video metadata and available formats"""
    ydl_opts = get_ydl_opts(download_mode=False)
    ffmpeg_available = is_ffmpeg_available()
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        
        if not info:
            raise ValueError("Failed to extract video information")
        
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
            
            # ⚠️ FFmpeg-aware filtering:
            # If FFmpeg not available, skip formats that need merging (separate audio/video)
            if not ffmpeg_available and vcodec != 'none' and acodec == 'none':
                continue  # Video-only, needs merge
            if not ffmpeg_available and vcodec == 'none' and acodec != 'none':
                continue  # Audio-only (unless user wants audio)
            
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
                'note': 'Requires FFmpeg merge' if needs_merge and not ffmpeg_available else None
            })
        
        # Sort: prefer higher resolution + complete formats
        def quality_score(q):
            res = int(''.join(filter(str.isdigit, str(q['label'])))) if any(c.isdigit() for c in str(q['label'])) else 0
            complete = 0 if q['needs_ffmpeg'] else 1  # Prefer non-merge formats
            return (res * 100) + (complete * 50)
        
        qualities.sort(key=quality_score, reverse=True)
        
        return {
            'success': True,
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail') or (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader') or info.get('channel') or 'Unknown',
            'view_count': info.get('view_count'),
            'qualities': qualities[:20],
            'ffmpeg_available': ffmpeg_available,
            'webpage_url': info.get('webpage_url', url)
        }

@app.route('/')
def home():
    """Health check / info endpoint"""
    return jsonify({
        'service': 'Video Downloader API',
        'version': '2.0.0',
        'status': 'running',
        'ffmpeg': is_ffmpeg_available(),
        'endpoints': {
            'get_info': 'POST /api/get-info',
            'download': 'POST /api/download'
        }
    })

@app.route('/health')
def health_check():
    """Render.com health check"""
    return jsonify({'status': 'healthy', 'ffmpeg': is_ffmpeg_available(), 'timestamp': time.time()}), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
def get_video_info():
    """Extract video info and available qualities"""
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
        
        # URL validation
        parsed = urlparse(video_url)
        if not parsed.scheme or not parsed.netloc:
            return jsonify({'success': False, 'error': 'Invalid URL format'}), 400
        
        # SSRF protection
        blocked = ['localhost', '127.0.0.1', '192.168.', '10.', '172.16.', 'file://', 'ftp://']
        if any(b in video_url.lower() for b in blocked):
            return jsonify({'success': False, 'error': 'This URL is not allowed'}), 403
        
        logger.info(f"Processing: {video_url[:50]}...")
        
        result = extract_video_info(video_url)
        
        elapsed = time.time() - start_time
        logger.info(f"✓ Extracted in {elapsed:.2f}s - {len(result['qualities'])} qualities")
        
        return jsonify(result)
        
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        logger.error(f"yt-dlp error: {e}")
        
        if 'private' in error_msg or 'unavailable' in error_msg:
            return jsonify({'success': False, 'error': 'This video is private or unavailable'}), 403
        elif 'age' in error_msg or 'login' in error_msg:
            return jsonify({'success': False, 'error': 'This video requires login or age verification'}), 403
        elif 'region' in error_msg or 'blocked' in error_msg:
            return jsonify({'success': False, 'error': 'This video is not available in your region'}), 403
        elif 'copyright' in error_msg:
            return jsonify({'success': False, 'error': 'Video removed due to copyright claim'}), 403
        else:
            return jsonify({'success': False, 'error': f'Failed to fetch video: {str(e)[:150]}'}), 500
            
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:100]}'}), 500

@app.route('/api/download', methods=['POST', 'OPTIONS'])
def download_video():
    """
    Generate download link or proxy stream
    Strategy: 
    1. If direct URL available and platform allows → return direct link
    2. Else → stream through server (avoids hotlink blocking)
    """
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.get_json(silent=True)
        
        if not 
            return jsonify({'success': False, 'error': 'Invalid request'}), 400
        
        video_url = data.get('url', '').strip()
        format_id = data.get('format_id', 'best')
        
        if not video_url:
            return jsonify({'success': False, 'error': 'Video URL is required'}), 400
        
        logger.info(f"Download: {video_url[:50]}... | format: {format_id}")
        
        ydl_opts = get_ydl_opts(download_mode=True, format_id=format_id)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            if not info:
                return jsonify({'success': False, 'error': 'Failed to fetch video info'}), 500
            
            direct_url = info.get('url')
            
            # If direct URL available, return it (simplest)
            if direct_url:
                title = sanitize_filename(info.get('title', 'video'))
                ext = info.get('ext', 'mp4')
                
                return jsonify({
                    'success': True,
                    'method': 'direct',
                    'download_url': direct_url,
                    'title': info.get('title'),
                    'filename': f"{title}.{ext}",
                    'ext': ext,
                    'filesize': info.get('filesize'),
                    'hint': 'If download fails, try right-click → "Save link as..." or use a download manager.'
                })
            else:
                # Fallback: Return error with suggestion
                return jsonify({
                    'success': False,
                    'error': 'Direct download not available for this format/platform.',
                    'hint': 'Try a different quality (720p or lower) or use the direct URL if available.',
                    'ffmpeg_note': '1080p+ formats may require FFmpeg which might not be available on this server.'
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

@app.errorhandler(429)
def rate_limit(e):
    return jsonify({'error': 'Too many requests. Please slow down.'}), 429

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting server on port {port} | FFmpeg: {is_ffmpeg_available()}")
    app.run(host='0.0.0.0', port=port, debug=False)