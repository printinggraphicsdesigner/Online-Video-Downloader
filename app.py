"""
🎬 Professional Video Downloader - 1080p+ with FFmpeg Merging
Supports: Instagram, TikTok, Twitter, Pinterest, Vimeo, Facebook
"""

from flask import Flask, request, jsonify, Response, send_file, stream_with_context
from flask_cors import CORS
import yt_dlp
import logging
import os
import re
import time
import shutil
import subprocess
import requests
from urllib.parse import urlparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"]}})
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024
app.config['TEMP_FOLDER'] = '/app/temp'

# Ensure temp folder exists
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)

def detect_site(url):
    """Detect which site the URL is from"""
    url_lower = url.lower()
    if 'instagram.com' in url_lower or 'instagr.am' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    elif 'pinterest.com' in url_lower or 'pin.it' in url_lower:
        return 'pinterest'
    elif 'vimeo.com' in url_lower:
        return 'vimeo'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
        return 'facebook'
    else:
        return 'generic'

def is_ffmpeg_available():
    """Check if FFmpeg is installed"""
    return shutil.which('ffmpeg') is not None

def get_ydl_opts(site='generic', download_mode=False):
    """Get yt-dlp options for the site"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificate': True,
        'noplaylist': True,
        'ignoreerrors': False,
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 60,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'geo_bypass': True,
        'extract_flat': False if not download_mode else 'in_playlist',
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
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
    elif site == 'facebook':
        opts.update({
            'extractor_args': {'facebook': {}},
            'http_headers': {'Referer': 'https://www.facebook.com/'}
        })
    
    return opts

def format_qualities(formats, ffmpeg_available=False):
    """Format ALL available qualities (including separate video/audio for merging)"""
    qualities = []
    seen_labels = set()
    
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
        url = fmt.get('url')
        
        if not url:
            continue
        
        if vcodec == 'none' and acodec == 'none':
            continue
        
        # Audio only - collect separately
        if vcodec == 'none' and acodec != 'none':
            audio_label = 'Audio Only'
            tbr = fmt.get('tbr', 0)
            if tbr > 0:
                audio_label = f"Audio Only {int(tbr)}k"
            
            audio_formats.append({
                'format_id': fmt_id,
                'label': audio_label,
                'ext': ext,
                'filesize': filesize,
                'vcodec': '✗',
                'acodec': '✓',
                'url': url,
            })
            continue
        
        # Video formats - include ALL (even if needs merging)
        if height > 0 and vcodec != 'none':
            label = None
            for std_height, std_label in sorted(quality_map.items(), reverse=True):
                if height >= std_height:
                    label = std_label
                    break
            
            if not label:
                label = f"{height}p"
            
            if fps and fps > 30 and fps not in [30, 60]:
                label += f" {int(fps)}fps"
            
            # Check if format needs merging (separate audio)
            needs_merge = acodec == 'none'
            
            if label not in video_formats or height > video_formats[label].get('height', 0):
                video_formats[label] = {
                    'format_id': fmt_id,
                    'label': label,
                    'ext': ext,
                    'filesize': filesize,
                    'vcodec': '✓',
                    'acodec': '✓' if not needs_merge else '✗',
                    'url': url,
                    'height': height,
                    'fps': fps,
                    'needs_merge': needs_merge,
                    'note': 'Requires FFmpeg merge' if needs_merge and ffmpeg_available else None,
                }
    
    # Sort video formats by height
    sorted_labels = sorted(
        video_formats.keys(),
        key=lambda x: int(''.join(filter(str.isdigit, x)) or '0'),
        reverse=True
    )
    
    for label in sorted_labels:
        qualities.append(video_formats[label])
    
    # Add audio-only at the end
    if audio_formats:
        qualities.append(audio_formats[0])
    
    return qualities

def extract_video_info(url, site, max_attempts=3):
    """Extract video info with retries"""
    ffmpeg_available = is_ffmpeg_available()
    logger.info(f"FFmpeg available: {ffmpeg_available}")
    
    for attempt in range(max_attempts):
        try:
            logger.info(f"Attempt {attempt + 1}/{max_attempts} for {site}")
            
            opts = get_ydl_opts(site)
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise ValueError("No information returned")
                
                formats = info.get('formats', [])
                if not formats and info.get('url'):
                    formats = [info]
                
                logger.info(f"Found {len(formats)} formats")
                
                qualities = format_qualities(formats, ffmpeg_available)
                logger.info(f"Formatted {len(qualities)} qualities (FFmpeg: {ffmpeg_available})")
                
                return {
                    'success': True,
                    'title': info.get('title', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader') or info.get('channel') or 'Unknown',
                    'view_count': info.get('view_count'),
                    'qualities': qualities,
                    'ffmpeg_available': ffmpeg_available,
                    'platform': site,
                }
                
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_attempts - 1:
                if 'private' in error_msg or 'unavailable' in error_msg:
                    raise ValueError("Video is private or unavailable")
                elif 'bot' in error_msg or 'authentication' in error_msg or 'sign in' in error_msg:
                    raise ValueError(f"{site.title()} is blocking automated requests. Try again later.")
                elif 'region' in error_msg or 'blocked' in error_msg:
                    raise ValueError("Video not available in your region")
                elif '403' in error_msg or 'forbidden' in error_msg:
                    raise ValueError("Access forbidden by the site")
                elif 'copyright' in error_msg or 'removed' in error_msg:
                    raise ValueError("Video removed due to copyright")
                else:
                    raise ValueError(f"Failed to extract: {str(e)[:150]}")
            
            time.sleep(2 ** attempt)

def merge_video_audio(video_path, audio_path, output_path):
    """Merge video and audio using FFmpeg"""
    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-strict', 'experimental',
        '-movflags', '+faststart',
        '-y',  # Overwrite output
        output_path
    ]
    
    logger.info(f"Merging: {' '.join(cmd)}")
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300  # 5 minutes timeout
    )
    
    if result.returncode != 0:
        logger.error(f"FFmpeg merge failed: {result.stderr}")
        raise RuntimeError(f"FFmpeg merge failed: {result.stderr[:200]}")
    
    logger.info(f"✓ Merge successful: {output_path}")
    return output_path

def cleanup_temp_files(*file_paths):
    """Clean up temporary files"""
    for path in file_paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
                logger.info(f"Cleaned up: {path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {path}: {e}")

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '18.0.0',
        'status': 'running',
        'ffmpeg': is_ffmpeg_available(),
        'supported_sites': ['Instagram', 'TikTok', 'Twitter', 'Pinterest', 'Vimeo', 'Facebook'],
        'note': '1080p+ with audio via FFmpeg merging!'
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'ffmpeg': is_ffmpeg_available(),
        'version': '18.0.0'
    }), 200

@app.route('/api/get-info', methods=['POST', 'OPTIONS'])
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
        
        result = extract_video_info(url, site, max_attempts=3)
        return jsonify(result)
        
    except ValueError as e:
        logger.error(f"Extraction error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Server error: {e}")
        return jsonify({'success': False, 'error': f'Server error: {str(e)[:80]}'}), 500

@app.route('/api/download', methods=['GET', 'POST', 'OPTIONS'])
def download():
    """Download endpoint with FFmpeg merging support"""
    if request.method == 'OPTIONS':
        return '', 204
    
    temp_files = []
    
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
        ffmpeg_available = is_ffmpeg_available()
        
        # Get video info first
        opts = get_ydl_opts(site)
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("No info")
            
            # Find the format
            target_format = None
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    target_format = fmt
                    break
            
            if not target_format:
                # Fallback to best
                target_format = info
            
            # Check if merging is needed
            vcodec = target_format.get('vcodec', 'none')
            acodec = target_format.get('acodec', 'none')
            needs_merge = (vcodec != 'none' and acodec == 'none')
            
            logger.info(f"Download: format_id={format_id}, needs_merge={needs_merge}, ffmpeg={ffmpeg_available}")
            
            # If needs merge and FFmpeg available, do server-side merge
            if needs_merge and ffmpeg_available:
                # Find matching audio format
                audio_format = None
                for fmt in info.get('formats', []):
                    if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none':
                        audio_format = fmt
                        break
                
                if not audio_format:
                    raise ValueError("No matching audio format found for merging")
                
                # Generate unique temp file names
                timestamp = int(time.time())
                video_temp = os.path.join(app.config['TEMP_FOLDER'], f'video_{timestamp}_{target_format["format_id"]}.mp4')
                audio_temp = os.path.join(app.config['TEMP_FOLDER'], f'audio_{timestamp}_{audio_format["format_id"]}.m4a')
                output_temp = os.path.join(app.config['TEMP_FOLDER'], f'merged_{timestamp}.mp4')
                
                temp_files = [video_temp, audio_temp, output_temp]
                
                # Download video stream
                logger.info(f"Downloading video stream: {target_format.get('url')[:80]}...")
                video_opts = opts.copy()
                video_opts['format'] = target_format['format_id']
                video_opts['outtmpl'] = video_temp
                
                with yt_dlp.YoutubeDL(video_opts) as video_ydl:
                    video_ydl.download([url])
                
                if not os.path.exists(video_temp):
                    raise ValueError("Failed to download video stream")
                
                # Download audio stream
                logger.info(f"Downloading audio stream: {audio_format.get('url')[:80]}...")
                audio_opts = opts.copy()
                audio_opts['format'] = audio_format['format_id']
                audio_opts['outtmpl'] = audio_temp
                
                with yt_dlp.YoutubeDL(audio_opts) as audio_ydl:
                    audio_ydl.download([url])
                
                if not os.path.exists(audio_temp):
                    raise ValueError("Failed to download audio stream")
                
                # Merge using FFmpeg
                logger.info("Merging video and audio with FFmpeg...")
                merge_video_audio(video_temp, audio_temp, output_temp)
                
                if not os.path.exists(output_temp):
                    raise ValueError("FFmpeg merge failed - output file not created")
                
                # Return direct URL to the merged file (proxy endpoint will stream it)
                title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))
                title = re.sub(r'\s+', '_', title.strip())[:100]
                
                # Clean up video and audio temp files (keep merged for streaming)
                cleanup_temp_files(video_temp, audio_temp)
                
                return jsonify({
                    'success': True,
                    'method': 'merged',
                    'download_url': f'/api/stream-file?path={output_temp}&filename={title}.mp4',
                    'title': info.get('title'),
                    'filename': f"{title}.mp4",
                    'merged': True,
                })
            
            else:
                # Direct download (no merge needed)
                download_url = target_format.get('url') or info.get('url')
                
                if not download_url:
                    raise ValueError("No download URL found")
                
                title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))
                title = re.sub(r'\s+', '_', title.strip())[:100]
                ext = target_format.get('ext', 'mp4')
                
                return jsonify({
                    'success': True,
                    'method': 'direct',
                    'download_url': download_url,
                    'title': info.get('title'),
                    'filename': f"{title}.{ext}",
                    'merged': False,
                })
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        # Clean up any temp files on error
        cleanup_temp_files(*temp_files)
        return jsonify({'success': False, 'error': f'Failed: {str(e)[:100]}'}), 500

@app.route('/api/stream-file')
def stream_file():
    """Stream a merged file from temp folder"""
    try:
        file_path = request.args.get('path')
        filename = request.args.get('filename', 'video.mp4')
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        # Stream the file
        def generate():
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk
        
        response = Response(
            stream_with_context(generate()),
            mimetype='video/mp4',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'video/mp4',
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*',
            }
        )
        
        # Clean up after streaming starts (file will be deleted after response)
        @response.call_on_close
        def cleanup():
            cleanup_temp_files(file_path)
        
        return response
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({'error': f'Stream failed: {str(e)[:100]}'}), 500

@app.route('/api/proxy-download')
def proxy_download():
    """Proxy download for direct URLs"""
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

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    logger.info(f"🚀 Starting Video Downloader v18.0.0 on port {port}")
    logger.info(f"✅ FFmpeg available: {is_ffmpeg_available()}")
    logger.info(f"✅ Temp folder: {app.config['TEMP_FOLDER']}")
    app.run(host='0.0.0.0', port=port, debug=False)
