"""In-network HTTP client (Python standard library only), run with `docker exec -i`.

stdin: one JSON request spec {url, method, headers, body, timeout}.
stdout: one JSON object {status, body} (body is the response text).
The test network is closed (`--internal`), so requests are made from inside it.
"""
import json
import sys
import urllib.error
import urllib.request

spec = json.load(sys.stdin)
data = json.dumps(spec['body']).encode() if spec.get('body') is not None else None
request = urllib.request.Request(spec['url'], data=data, method=spec.get('method', 'POST'),
                                 headers={'Content-Type': 'application/json', **spec.get('headers', {})})
try:
    with urllib.request.urlopen(request, timeout=spec.get('timeout', 60)) as response:
        status, body = response.status, response.read()
except urllib.error.HTTPError as error:
    status, body = error.code, error.read()
print(json.dumps({'status': status, 'body': body.decode('utf-8', 'replace')}))
