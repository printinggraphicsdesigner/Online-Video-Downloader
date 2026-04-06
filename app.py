"""
🎬 Professional Video Downloader - 1080p+ with FFmpeg Merging
Supports: Instagram, TikTok, Twitter, Pinterest, Vimeo, Facebook
"""

from flask import Flask, request, jsonify, Response, stream_with_context
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
    elif 'reddit.com' in url_lower or 'redd.it' in url_lower:
        return 'reddit'
    elif 'pinterest.com' in url_lower or 'pin.it' in url_lower:
        return 'pinterest'
    elif 'vimeo.com' in url_lower:
        return 'vimeo'
    elif 'dailymotion.com' in url_lower or 'dai.ly' in url_lower:
        return 'dailymotion'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower or 'fb.com' in url_lower:
        return 'facebook'
    else:
        return 'generic'

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
    
    # ✅ Dailymotion-specific options
    if site == 'dailymotion':
        opts.update({
            'extractor_args': {'dailymotion': {}},
            'http_headers': {
                'Referer': 'https://www.dailymotion.com/',
                'Origin': 'https://www.dailymotion.com',
            }
        })
    
    # ✅ Facebook-specific options
    elif site == 'facebook':
        opts.update({
            'extractor_args': {'facebook': {}},
            'http_headers': {
                'Referer': 'https://www.facebook.com/',
            }
        })
    
    # ✅ Instagram
    elif site == 'instagram':
        opts.update({
            'extractor_args': {'instagram': {'include_formats': True}},
            'http_headers': {'X-IG-App-ID': '936619743392459'}
        })
    
    # ✅ TikTok
    elif site == 'tiktok':
        opts.update({
            'extractor_args': {'tiktok': {'embed_source': 'embed'}},
            'http_headers': {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)'}
        })
    
    # ✅ Twitter/X
    elif site == 'twitter':
        opts.update({
            'extractor_args': {'twitter': {}},
            'http_headers': {'Referer': 'https://twitter.com/'}
        })
    
    # ✅ Reddit
    elif site == 'reddit':
        opts.update({
            'extractor_args': {'reddit': {}},
            'http_headers': {'Referer': 'https://www.reddit.com/'}
        })
    
    # ✅ Pinterest
    elif site == 'pinterest':
        opts.update({
            'extractor_args': {'pinterest': {}},
            'http_headers': {'Referer': 'https://www.pinterest.com/'}
        })
    
    # ✅ Vimeo
    elif site == 'vimeo':
        opts.update({
            'extractor_args': {'vimeo': {'force_noplaylist': True}},
            'http_headers': {'Referer': 'https://vimeo.com/'}
        })
    
    return opts

def format_qualities(formats, ffmpeg_available=False):
    """Format ALL available qualities"""
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
        
        # Audio only
        if vcodec == 'none' and acodec != 'none':
            audio_label = 'Audio Only'
            tbr = fmt.get('tbr', 0)
            if tbr and tbr > 0:
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
        
        # Video formats
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
    
    sorted_labels = sorted(
        video_formats.keys(),
        key=lambda x: int(''.join(filter(str.isdigit, x)) or '0'),
        reverse=True
    )
    
    for label in sorted_labels:
        qualities.append(video_formats[label])
    
    if audio_formats:
        qualities.append(audio_formats[0])
    
    return qualities

def merge_video_audio(video_path, audio_path, output_path):
    """Merge video and audio using FFmpeg"""
    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        '-y',
        output_path
    ]
    
    logger.info(f"FFmpeg command: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    
    if result.returncode != 0:
        logger.error(f"FFmpeg stderr: {result.stderr}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[:200]}")
    
    if not os.path.exists(output_path):
        raise RuntimeError("Output file not created")
    
    file_size = os.path.getsize(output_path)
    if file_size < 1024:
        raise RuntimeError(f"Output file too small: {file_size} bytes")
    
    logger.info(f"✓ Merge successful: {output_path} ({file_size / 1024 / 1024:.2f} MB)")
    return output_path

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
                logger.info(f"Formatted {len(qualities)} qualities")
                
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
                
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e).lower()
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if attempt == max_attempts - 1:
                # ✅ Better error messages for specific sites
                if site == 'dailymotion' and 'impersonation' in error_msg:
                    raise ValueError("Dailymotion requires browser impersonation support. Please contact the site administrator to install curl_cffi library.")
                elif site == 'facebook' and ('login' in error_msg or 'unsupported url' in error_msg):
                    raise ValueError("This Facebook video requires login. Only public videos are supported without cookies.")
                elif 'private' in error_msg or 'unavailable' in error_msg:
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

@app.route('/')
def home():
    return jsonify({
        'service': 'Video Downloader',
        'version': '19.0.0',
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
        'version': '19.0.0'
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
                logger.error("Invalid JSON request")
                return jsonify({'success': False, 'error': 'Invalid request'}), 400
            url = data.get('url', '').strip()
            format_id = data.get('format_id', 'best')
        
        if not url:
            logger.error("No URL provided")
            return jsonify({'success': False, 'error': 'URL required'}), 400
        
        logger.info(f"Download request: url={url[:50]}..., format_id={format_id}")
        
        site = detect_site(url)
        ffmpeg_available = is_ffmpeg_available()
        logger.info(f"Site: {site}, FFmpeg available: {ffmpeg_available}")
        
        opts = get_ydl_opts(site)
        
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                logger.error("No info returned from yt-dlp")
                raise ValueError("No info")
            
            # Find the format
            target_format = None
            for fmt in info.get('formats', []):
                if fmt.get('format_id') == format_id:
                    target_format = fmt
                    logger.info(f"Found format: {fmt.get('format_id')} - {fmt.get('height')}p")
                    break
            
            if not target_format:
                logger.warning(f"Format {format_id} not found, using best")
                target_format = info
            
            vcodec = target_format.get('vcodec', 'none')
            acodec = target_format.get('acodec', 'none')
            needs_merge = (vcodec != 'none' and acodec == 'none')
            
            logger.info(f"vcodec={vcodec}, acodec={acodec}, needs_merge={needs_merge}")
            
            # === CASE 1: Needs merge AND FFmpeg available ===
            if needs_merge and ffmpeg_available:
                # ✅ FIX: Handle None values in tbr when sorting
                def safe_tbr(fmt):
                    tbr = fmt.get('tbr')
                    return tbr if tbr is not None else 0
                
                # Find matching audio format (best quality audio)
                audio_format = None
                for fmt in sorted(info.get('formats', []), key=safe_tbr, reverse=True):
                    if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none':
                        audio_format = fmt
                        logger.info(f"Found audio format: {fmt.get('format_id')} - {fmt.get('tbr')}k")
                        break
                
                if not audio_format:
                    logger.error("No audio format found for merging")
                    raise ValueError("No matching audio format found")
                
                # Generate unique temp file names
                timestamp = int(time.time())
                video_temp = os.path.join(app.config['TEMP_FOLDER'], f'v_{timestamp}_{target_format["format_id"]}.mp4')
                audio_temp = os.path.join(app.config['TEMP_FOLDER'], f'a_{timestamp}_{audio_format["format_id"]}.m4a')
                output_temp = os.path.join(app.config['TEMP_FOLDER'], f'm_{timestamp}.mp4')
                
                temp_files = [video_temp, audio_temp, output_temp]
                
                try:
                    # Download video stream
                    logger.info(f"Downloading video to: {video_temp}")
                    video_opts = opts.copy()
                    video_opts['format'] = target_format['format_id']
                    video_opts['outtmpl'] = video_temp
                    video_opts['noplaylist'] = True
                    
                    with yt_dlp.YoutubeDL(video_opts) as video_ydl:
                        video_ydl.download([url])
                    
                    if not os.path.exists(video_temp):
                        logger.error(f"Video file not created: {video_temp}")
                        raise ValueError("Failed to download video stream")
                    
                    video_size = os.path.getsize(video_temp)
                    logger.info(f"Video downloaded: {video_size / 1024 / 1024:.2f} MB")
                    
                    if video_size < 1024:
                        logger.error(f"Video file too small: {video_size} bytes")
                        raise ValueError("Video file corrupted or too small")
                    
                    # Download audio stream
                    logger.info(f"Downloading audio to: {audio_temp}")
                    audio_opts = opts.copy()
                    audio_opts['format'] = audio_format['format_id']
                    audio_opts['outtmpl'] = audio_temp
                    audio_opts['noplaylist'] = True
                    
                    with yt_dlp.YoutubeDL(audio_opts) as audio_ydl:
                        audio_ydl.download([url])
                    
                    if not os.path.exists(audio_temp):
                        logger.error(f"Audio file not created: {audio_temp}")
                        raise ValueError("Failed to download audio stream")
                    
                    audio_size = os.path.getsize(audio_temp)
                    logger.info(f"Audio downloaded: {audio_size / 1024:.2f} KB")
                    
                    if audio_size < 1024:
                        logger.error(f"Audio file too small: {audio_size} bytes")
                        raise ValueError("Audio file corrupted or too small")
                    
                    # Merge using FFmpeg
                    logger.info("Starting FFmpeg merge...")
                    merge_video_audio(video_temp, audio_temp, output_temp)
                    
                    if not os.path.exists(output_temp):
                        logger.error(f"Merged file not created: {output_temp}")
                        raise ValueError("FFmpeg merge failed - output not created")
                    
                    output_size = os.path.getsize(output_temp)
                    logger.info(f"Merged file: {output_size / 1024 / 1024:.2f} MB")
                    
                    if output_size < 1024:
                        logger.error(f"Merged file too small: {output_size} bytes")
                        raise ValueError("Merged file corrupted")
                    
                    # Clean up source files
                    cleanup_temp_files(video_temp, audio_temp)
                    
                    title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))
                    title = re.sub(r'\s+', '_', title.strip())[:100]
                    
                    stream_url = f'/api/stream-file?path={output_temp}&filename={title}.mp4'
                    
                    logger.info(f"✓ Merge successful, returning stream URL")
                    
                    return jsonify({
                        'success': True,
                        'method': 'merged',
                        'download_url': stream_url,
                        'title': info.get('title'),
                        'filename': f"{title}.mp4",
                        'merged': True,
                        'ffmpeg_used': True,
                    })
                    
                except Exception as merge_error:
                    logger.error(f"Merge process failed: {merge_error}", exc_info=True)
                    cleanup_temp_files(*temp_files)
                    raise ValueError(f"Merging failed: {str(merge_error)[:150]}")
            
            # === CASE 2: Direct download (no merge needed) ===
            else:
                download_url = target_format.get('url') or info.get('url')
                
                if not download_url:
                    logger.error("No download URL found")
                    raise ValueError("No download URL found")
                
                title = re.sub(r'[^\w\s\.\-]', '', info.get('title', 'video'))
                title = re.sub(r'\s+', '_', title.strip())[:100]
                ext = target_format.get('ext', 'mp4')
                
                logger.info(f"Direct download: {download_url[:80]}...")
                
                return jsonify({
                    'success': True,
                    'method': 'direct',
                    'download_url': download_url,
                    'title': info.get('title'),
                    'filename': f"{title}.{ext}",
                    'merged': False,
                })
                
    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        cleanup_temp_files(*temp_files)
        return jsonify({'success': False, 'error': f'Failed: {str(e)[:150]}'}), 500

@app.route('/api/stream-file')
def stream_file():
    """Stream a merged file from temp folder"""
    try:
        file_path = request.args.get('path')
        filename = request.args.get('filename', 'video.mp4')
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
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
    logger.info(f"🚀 Starting Video Downloader v19.0.0 on port {port}")
    logger.info(f"✅ FFmpeg available: {is_ffmpeg_available()}")
    logger.info(f"✅ Temp folder: {app.config['TEMP_FOLDER']}")
    app.run(host='0.0.0.0', port=port, debug=False)
