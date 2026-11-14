"""Recording stand-in for an upstream provider (Python standard library only).

Run inside a container on the closed test network:

    python standin.py --label meridian --port 3456 --out /records/meridian.jsonl

Every request is appended to --out as one JSON line: label, method, path, the
`model` field of a JSON body, and whether the body asked for streaming.  The
answer is deliberately dull: a valid Anthropic message for POST /v1/messages
(so a routed request completes) and a 500 for anything else.  Nobody should
read meaning into the answer; the log is the evidence.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()


def make_handler(label, out_path):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):  # keep container logs quiet
            pass

        def _record_and_answer(self):
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b''
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {}
            entry = {
                'label': label, 'time': time.time(), 'method': self.command, 'path': self.path,
                'model': body.get('model') if isinstance(body, dict) else None,
                'stream': bool(body.get('stream')) if isinstance(body, dict) else False,
            }
            with LOCK, open(out_path, 'a') as handle:
                handle.write(json.dumps(entry) + '\n')
            if self.command == 'POST' and self.path.split('?')[0].endswith('/v1/messages'):
                payload = {'id': 'msg_standin', 'type': 'message', 'role': 'assistant',
                           'model': entry['model'] or 'standin',
                           'content': [{'type': 'text', 'text': 'recorded'}],
                           'stop_reason': 'end_turn', 'stop_sequence': None,
                           'usage': {'input_tokens': 1, 'output_tokens': 1}}
                status = 200
            else:
                payload = {'error': {'type': 'standin', 'message': 'recording stand-in'}}
                status = 500
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _record_and_answer

    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    open(args.out, 'a').close()
    ThreadingHTTPServer(('0.0.0.0', args.port), make_handler(args.label, args.out)).serve_forever()
