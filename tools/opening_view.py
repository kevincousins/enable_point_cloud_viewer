"""Set (or re-set) the view a viewer project opens on: camera + crop box.

    python tools/opening_view.py "<Client>" <project id>        e.g.  "BDA" jarvis
    python tools/opening_view.py "<Client>" <project id> --port 8090 --no-browser

Starts a local web server (127.0.0.1 only) that serves the viewer from site/,
and opens the project in the browser. Because the page is on this PC, the
viewer shows a "Local check" bar at the bottom:

  1. Frame the view, and size the crop box to what the client should see first.
  2. Press "Set as opening view". The tool writes it into the client's
     projects.js as that project's `initialView` (camera + crop box).
     "Clear" removes it, so the link opens on the whole scan again.
  3. Reload the page to check it opens there.

Nothing goes live until that projects.js is committed and pushed (Netlify then
publishes it). The server stops on Ctrl+C, or after 2 hours.

Standard library only. Node is used to check the edited projects.js still loads
and that only this project's `initialView` changed; without node it refuses.
"""
import argparse, functools, http.server, json, math, os, re, shutil, subprocess, sys
import threading, time, urllib.parse, webbrowser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(REPO, 'site')
CLIENTS = os.path.join(SITE, 'clients')
ENDPOINT = '/__enable/opening-view'
PID_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')
MARK = '// Opening view: set with tools/opening_view.py'
LIFETIME_S = 2 * 60 * 60


class Refused(Exception):
    pass


# ---------- projects.js editing ----------

def _skip(text, i):
    """If text[i] starts a string or comment, return the index just past it; else None."""
    ch = text[i]
    if ch in '"\'`':
        j = i + 1
        while j < len(text):
            if text[j] == '\\':
                j += 2
                continue
            if text[j] == ch:
                return j + 1
            j += 1
        raise Refused('projects.js has an unterminated string')
    if text.startswith('//', i):
        j = text.find('\n', i)
        return len(text) if j < 0 else j
    if text.startswith('/*', i):
        j = text.find('*/', i + 2)
        if j < 0:
            raise Refused('projects.js has an unterminated comment')
        return j + 2
    return None


def match_brace(text, open_i):
    depth, i = 0, open_i
    while i < len(text):
        j = _skip(text, i)
        if j is not None:
            i = j
            continue
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise Refused('projects.js has unbalanced braces')


def object_keys(text, open_i, close_i):
    """Direct keys of the object literal text[open_i..close_i] whose value is an
    object: {name: (key_start, value_open_brace)}."""
    keys, depth, i = {}, 0, open_i
    key_re = re.compile(r'(["\']?)([A-Za-z0-9_$.\-]+)\1\s*:\s*\{')
    prev = ''
    while i <= close_i:
        if depth == 1 and prev in ('{', ','):
            m = key_re.match(text, i)
            if m:
                keys[m.group(2)] = (i, m.end() - 1)
        j = _skip(text, i)
        if j is not None:
            i = j
            continue
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if not ch.isspace():
            prev = ch
        i += 1
    return keys


def num(v, what):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise Refused(f'{what} is not a number')
    return round(float(v), 6)


def clean_view(view):
    """Rebuild the view from known keys only, so nothing but our own numbers is written."""
    if not isinstance(view, dict):
        raise Refused('view must be an object')
    out = {}

    def v3(o, what):
        if not isinstance(o, dict):
            raise Refused(f'{what} missing')
        return {k: num(o.get(k), f'{what}.{k}') for k in ('x', 'y', 'z')}

    cam = view.get('cam')
    if cam is not None:
        mode = cam.get('mode', 1)
        if mode not in (0, 1):
            raise Refused('cam.mode must be 0 or 1')
        out['cam'] = {'position': v3(cam.get('position'), 'cam.position'),
                      'yaw': num(cam.get('yaw'), 'cam.yaw'),
                      'pitch': num(cam.get('pitch'), 'cam.pitch'),
                      'radius': num(cam.get('radius'), 'cam.radius'),
                      'mode': mode}
    clip = view.get('clip')
    if clip is not None:
        ini = clip.get('initial') if isinstance(clip, dict) else None
        out['clip'] = {'initial': {'position': v3(ini and ini.get('position'), 'clip.position'),
                                   'rotation': v3(ini and ini.get('rotation'), 'clip.rotation'),
                                   'scale': v3(ini and ini.get('scale'), 'clip.scale')}}
    if not out:
        raise Refused('view has neither a camera nor a crop box')
    return out


def fmt_num(x):
    s = f'{x:.6f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def fmt_v3(o):
    return '{ x: %s, y: %s, z: %s }' % (fmt_num(o['x']), fmt_num(o['y']), fmt_num(o['z']))


def view_js(view, indent, comment=True):
    i2 = indent + '  '
    lines = [f'{indent}{MARK} ({time.strftime("%Y-%m-%d")}).'] if comment else []
    lines.append(f'{indent}initialView: {{')
    parts = []
    if 'cam' in view:
        c = view['cam']
        parts.append(f'{i2}cam: {{ position: {fmt_v3(c["position"])}, yaw: {fmt_num(c["yaw"])}, '
                     f'pitch: {fmt_num(c["pitch"])}, radius: {fmt_num(c["radius"])}, mode: {c["mode"]} }}')
    if 'clip' in view:
        c = view['clip']['initial']
        parts.append(f'{i2}clip: {{ initial: {{ position: {fmt_v3(c["position"])}, '
                     f'rotation: {fmt_v3(c["rotation"])}, scale: {fmt_v3(c["scale"])} }} }}')
    lines.append(',\n'.join(parts))
    lines.append(f'{indent}}},')
    return '\n'.join(lines) + '\n'


def set_initial_view(text, pid, view):
    """text uses LF. Returns new text with the entry's initialView replaced (view=None removes it)."""
    m = re.search(r'window\.PROJECTS\s*=\s*\{', text)
    if not m:
        raise Refused('projects.js has no `window.PROJECTS = {`')
    p_open = m.end() - 1
    entries = object_keys(text, p_open, match_brace(text, p_open))
    if pid not in entries:
        raise Refused(f'no project "{pid}" in this projects.js')
    e_open = entries[pid][1]
    e_close = match_brace(text, e_open)

    # Remove the old initialView (and our marker comment above it), if any.
    inner = object_keys(text, e_open, e_close)
    if 'initialView' in inner:
        k_start, k_open = inner['initialView']
        k_close = match_brace(text, k_open)
        start = text.rfind('\n', 0, k_start) + 1
        if text[start:k_start].strip():          # something else on that line: cut from the key
            start = k_start
        else:                                    # drop our marker comment line just above
            prev_start = text.rfind('\n', 0, start - 1) + 1
            if start > 0 and text[prev_start:start].strip().startswith(MARK):
                start = prev_start
        end = k_close + 1
        rest = re.match(r'[ \t]*,?[ \t]*(\n)?', text[end:])
        end += rest.end()
        text = text[:start] + text[end:]
        e_close = match_brace(text, e_open)

    if view is None:
        return text
    nl = text.find('\n', e_open, e_close)
    if nl < 0:                                   # one-line entry
        snippet = view_js(view, '', comment=False).replace('\n', ' ')
        return text[:e_open + 1] + ' ' + snippet.strip() + ' ' + text[e_open + 1:]
    nxt = text[nl + 1:e_close + 1]
    indent = re.match(r'[ \t]*', nxt).group(0) or '    '
    return text[:nl + 1] + view_js(view, indent) + text[nl + 1:]


NODE_DUMP = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { window: {} }; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
process.stdout.write(JSON.stringify(ctx.window.PROJECTS || null));
"""


def node_dump(path):
    node = shutil.which('node')
    if not node:
        raise Refused('node is not installed, so the edited projects.js cannot be checked')
    r = subprocess.run([node, '-e', NODE_DUMP, path], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise Refused('projects.js does not load in node: ' + (r.stderr.strip().splitlines() or ['?'])[-1])
    return json.loads(r.stdout)


def write_projects(client, pid, view):
    path = os.path.join(CLIENTS, client, 'projects.js')
    raw = open(path, 'rb').read().decode('utf-8')
    crlf = '\r\n' in raw
    old = raw.replace('\r\n', '\n')
    new = set_initial_view(old, pid, view)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write((new.replace('\n', '\r\n') if crlf else new).encode('utf-8'))
    try:
        before, after = node_dump(path), node_dump(tmp)
        if not after or set(before) != set(after):
            raise Refused('the edit would change the list of projects')
        for k in before:
            b, a = dict(before[k]), dict(after[k])
            if k == pid:
                b.pop('initialView', None)
                got = a.pop('initialView', None)
                if got != view:
                    raise Refused('the saved initialView does not read back as sent')
            if a != b:
                raise Refused(f'the edit would change project "{k}" beyond its initialView')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


# ---------- server ----------

def client_from_page(page):
    parts = urllib.parse.unquote(page or '').strip('/').split('/')
    if len(parts) != 3 or parts[0] != 'clients' or not parts[2].endswith('.html'):
        raise Refused('the page is not a client viewer')
    client = parts[1]
    if client in ('', '.', '..', 'libs') or client not in os.listdir(CLIENTS):
        raise Refused('the page is not a client viewer')
    if not os.path.isfile(os.path.join(CLIENTS, client, 'projects.js')):
        raise Refused(f'no projects.js for client "{client}"')
    return client


class Handler(http.server.SimpleHTTPRequestHandler):
    lock = threading.Lock()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != ENDPOINT:
            return self.reply(404, {'ok': False, 'error': 'unknown address'})
        try:
            # Only this tool's own pages may save: same origin, JSON body.
            port = self.server.server_address[1]
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
                raise Refused('request from another site')
            if not (self.headers.get('Content-Type') or '').startswith('application/json'):
                raise Refused('expected JSON')
            n = int(self.headers.get('Content-Length') or 0)
            if n <= 0 or n > 20000:
                raise Refused('bad request size')
            req = json.loads(self.rfile.read(n).decode('utf-8'))
            client = client_from_page(req.get('page'))
            pid = req.get('p') or ''
            if not PID_RE.match(pid):
                raise Refused('no project id (?p=) on the page')
            view = None if req.get('view') is None else clean_view(req.get('view'))
            with self.lock:
                path = write_projects(client, pid, view)
            rel = os.path.relpath(path, REPO).replace('\\', '/')
            what = 'Opening view saved' if view else 'Opening view cleared'
            print(f'  {what}: {client} / {pid}  ->  {rel}', flush=True)
            self.reply(200, {'ok': True, 'message': f'{what} in {rel}. Reload to check it. '
                                                    'It goes live when projects.js is pushed.'})
        except Refused as e:
            print(f'  Refused: {e}', flush=True)
            self.reply(400, {'ok': False, 'error': str(e)})
        except Exception as e:  # keep serving; say what broke
            print(f'  Error: {e!r}', flush=True)
            self.reply(500, {'ok': False, 'error': 'the tool failed: ' + type(e).__name__})


def main():
    ap = argparse.ArgumentParser(description='Set the view a viewer project opens on.')
    ap.add_argument('client', help='client folder under site/clients, e.g. "BDA"')
    ap.add_argument('project', help="the project's id (the link's ?p=)")
    ap.add_argument('--port', type=int, default=8090)
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()

    try:
        client_from_page(f'/clients/{a.client}/viewer.html')
        text = open(os.path.join(CLIENTS, a.client, 'projects.js'), encoding='utf-8').read()
        m = re.search(r'window\.PROJECTS\s*=\s*\{', text)
        if not m or a.project not in object_keys(text, m.end() - 1, match_brace(text, m.end() - 1)):
            raise Refused(f'no project "{a.project}" in {a.client}/projects.js')
    except Refused as e:
        sys.exit(f'Refused: {e}')

    handler = functools.partial(Handler, directory=SITE)
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
            break
        except OSError:
            continue
    if not srv:
        sys.exit(f'Refused: no free port from {a.port} to {a.port + 19}')
    url = (f'http://127.0.0.1:{srv.server_address[1]}/clients/'
           f'{urllib.parse.quote(a.client)}/viewer.html?p={urllib.parse.quote(a.project)}')
    print(f'Opening view for {a.client} / {a.project}\n  {url}\n'
          f'  Frame it, then press "Set as opening view". Ctrl+C to stop (stops by itself after 2 hours).',
          flush=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if not a.no_browser:
        webbrowser.open(url)
    try:
        end = time.time() + LIFETIME_S
        while time.time() < end:
            time.sleep(1)
        print('Stopped after 2 hours.')
    except KeyboardInterrupt:
        print('Stopped.')
    finally:
        srv.shutdown()


if __name__ == '__main__':
    main()
