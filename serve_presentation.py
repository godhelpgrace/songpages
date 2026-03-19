import os
import json
import time
import cgi
import shutil
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from generate_html_index import generate_html_index

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PARAMS_FILENAME = 'presentation_params.json'
PARAMS_PATH = os.path.join(ROOT_DIR, PARAMS_FILENAME)
DEFAULT_SONG_DIR = os.path.join(ROOT_DIR, '灵栖清泉曲谱')
UPLOAD_DIR = DEFAULT_SONG_DIR if os.path.isdir(DEFAULT_SONG_DIR) else os.path.join(ROOT_DIR, 'uploads')


def merge_params(existing: dict, incoming: dict) -> dict:
    """Merge incoming params into existing structure.
    Expected incoming structure: { imageDir: str, items: { filename: {top:{}, bottom:{}} } }
    Stored structure: { dirs: { imageDir: { items: {...}, updatedAt: int } }, updatedAt: int }
    """
    if not isinstance(existing, dict):
        existing = {}
    dirs = existing.get('dirs')
    if not isinstance(dirs, dict):
        dirs = {}

    image_dir = incoming.get('imageDir')
    items = incoming.get('items') or {}
    if not image_dir or not isinstance(items, dict):
        return existing

    dir_entry = dirs.get(image_dir) or {}
    stored_items = dir_entry.get('items') or {}

    # Override provided filenames; keep others
    for filename, val in items.items():
        top = val.get('top', {}) or {}
        bottom = val.get('bottom', {}) or {}
        # 直接存储mask数组（若缺失则为空数组），用于遮盖刷持久化
        top_mask = top.get('mask') if isinstance(top.get('mask'), list) else []
        bottom_mask = bottom.get('mask') if isinstance(bottom.get('mask'), list) else []

        stored_items[filename] = {
            'top': {
                'offsetVh': int(top.get('offsetVh', 0)),
                'zoom': float(top.get('zoom', 1)),
                'mask': top_mask,
            },
            'bottom': {
                'offsetVh': int(bottom.get('offsetVh', 0)),
                'zoom': float(bottom.get('zoom', 1)),
                'mask': bottom_mask,
            },
        }

    now = int(time.time() * 1000)
    dir_entry['items'] = stored_items
    dir_entry['updatedAt'] = now
    dirs[image_dir] = dir_entry

    existing['dirs'] = dirs
    existing['updatedAt'] = now
    return existing


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT_DIR, **kwargs)

    def _set_cors_headers(self):
        # Allow cross-origin requests for saving params (from other localhost ports)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        # Handle CORS preflight for /save_params
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/upload':
            self.handle_upload()
            return
        if self.path != '/save_params':
            self.send_error(404, 'Not Found')
            return

        content_length = int(self.headers.get('Content-Length', '0'))
        try:
            raw = self.rfile.read(content_length) if content_length > 0 else b'{}'
            incoming = json.loads(raw.decode('utf-8') or '{}')
        except Exception as e:
            self.send_error(400, f'Invalid JSON: {e}')
            return

        try:
            existing = {}
            if os.path.exists(PARAMS_PATH):
                with open(PARAMS_PATH, 'r', encoding='utf-8') as f:
                    try:
                        existing = json.load(f)
                    except Exception:
                        existing = {}

            merged = merge_params(existing, incoming)
            with open(PARAMS_PATH, 'w', encoding='utf-8') as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)

            out = json.dumps({'status': 'ok', 'file': PARAMS_FILENAME}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(out)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(out)
        except Exception as e:
            # Return JSON with CORS headers on failure
            err = json.dumps({'status': 'error', 'message': f'{e}'}).encode('utf-8')
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(err)))
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(err)

    def handle_upload(self):
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            self.send_error(400, 'Invalid content type')
            return
        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    'REQUEST_METHOD': 'POST',
                    'CONTENT_TYPE': content_type,
                },
            )
        except Exception as e:
            self.send_error(400, f'Invalid form data: {e}')
            return
        if 'file' not in form:
            self.send_error(400, 'Missing file field')
            return
        file_items = form['file']
        if not isinstance(file_items, list):
            file_items = [file_items]
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        saved_files = []
        for item in file_items:
            if not getattr(item, 'filename', None):
                continue
            filename = os.path.basename(item.filename)
            name, ext = os.path.splitext(filename)
            ext = ext.lower()
            if ext not in {'.png', '.jpg', '.jpeg'}:
                continue
            target_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(target_path):
                suffix = 1
                while True:
                    candidate = os.path.join(UPLOAD_DIR, f"{name}_{suffix}{ext}")
                    if not os.path.exists(candidate):
                        target_path = candidate
                        break
                    suffix += 1
            try:
                with open(target_path, 'wb') as out:
                    shutil.copyfileobj(item.file, out)
                saved_files.append(os.path.basename(target_path))
            except Exception:
                continue
        if not saved_files:
            self.send_error(400, 'No valid image files uploaded')
            return
        try:
            generate_html_index(UPLOAD_DIR, os.path.join(ROOT_DIR, 'index.html'))
        except Exception as e:
            self.send_error(500, f'Generate html failed: {e}')
            return
        out = json.dumps({'status': 'ok', 'files': saved_files, 'dir': os.path.basename(UPLOAD_DIR)}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(out)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(out)


def main():
    port = int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f"Serving presentation at http://localhost:{port}/ (root: {ROOT_DIR})")
    print(f"Params file path: {PARAMS_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
