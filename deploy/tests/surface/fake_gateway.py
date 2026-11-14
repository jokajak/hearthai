"""Recording fake of the household gateway, OpenAI-compatible (Python standard library only).

Serves GET /v1/models (one local model, one cloud model) and POST
/v1/chat/completions (streaming and not) with short canned replies, and appends
one JSON line per request to --out: method, path, `model`, `stream`, and `kind`.

`kind` is how the test tells Open WebUI's own background calls from the user's
chat turns.  Open WebUI's task prompts all start with "### Task:" (default
templates in backend/open_webui/config.py at v0.11.3); the first line after it
names the task.  A request without that marker is a "chat" turn.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
MODELS = ('local-qwen', 'gpt-5.5')


def classify(body):
    messages = body.get('messages') or []
    text = ''
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            content = ' '.join(part.get('text', '') for part in content if isinstance(part, dict))
        text += f'\n{content or ""}'
    if '### Task:' not in text:
        return 'chat'
    after = text.split('### Task:', 1)[1].strip().splitlines()[0].lower()
    for needle, kind in (('title', 'task:title'), ('tags', 'task:tags'), ('follow-up', 'task:follow_ups')):
        if needle in after:
            return kind
    return 'task:other'


def canned(kind):
    if kind == 'task:title':
        return '{"title": "Fake gateway chat"}'
    if kind == 'task:tags':
        return '{"tags": ["testing", "fake"]}'
    if kind == 'task:follow_ups':
        return '{"follow_ups": ["What else?", "Tell me more", "Why?"]}'
    if kind.startswith('task:'):
        return '{}'
    return 'Canned reply from the fake gateway.'


def make_handler(out_path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def _send(self, status, payload, content_type='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _record(self, body=None):
            body = body if isinstance(body, dict) else {}
            entry = {'time': time.time(), 'method': self.command, 'path': self.path, 'model': body.get('model'),
                     'stream': bool(body.get('stream')),
                     'kind': classify(body) if self.path.split('?')[0].endswith('/chat/completions') else 'other'}
            with LOCK, open(out_path, 'a') as handle:
                handle.write(json.dumps(entry) + '\n')
            return entry

        def do_GET(self):
            self._record()
            if self.path.split('?')[0] in ('/v1/models', '/models'):
                self._send(200, {'object': 'list', 'data': [
                    {'id': name, 'object': 'model', 'created': 0, 'owned_by': 'fake'} for name in MODELS]})
            else:
                self._send(404, {'error': {'message': 'not found'}})

        def do_POST(self):
            length = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                body = {}
            entry = self._record(body)
            if not self.path.split('?')[0].endswith('/chat/completions'):
                return self._send(404, {'error': {'message': 'not found'}})
            text, model = canned(entry['kind']), body.get('model') or 'unknown'
            usage = {'prompt_tokens': 3, 'completion_tokens': 5, 'total_tokens': 8}
            if not entry['stream']:
                return self._send(200, {
                    'id': 'chatcmpl-fake', 'object': 'chat.completion', 'created': 0, 'model': model,
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
                    'usage': usage})
            chunks = [
                {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]},
                {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': usage},
            ]
            payload = ''.join(f'data: {json.dumps({"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": 0, "model": model, **c})}\n\n'
                              for c in chunks) + 'data: [DONE]\n\n'
            self._send(200, payload.encode(), 'text/event-stream')

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    open(args.out, 'a').close()
    ThreadingHTTPServer(('0.0.0.0', args.port), make_handler(args.out)).serve_forever()
