"""In-network HTTP client (Python standard library only).

The test network is `docker network create --internal`, so the host cannot
assume a route to it.  The test therefore runs this script inside a container
on that network (`docker exec -i ... python client.py`).  stdin is one JSON
request spec {url, method, headers, body}; stdout is one JSON object
{status, headers, body} where body is the raw response text, so the test sees
exactly what a client would: status line, headers, and body bytes as sent.
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
    with urllib.request.urlopen(request, timeout=spec.get('timeout', 120)) as response:
        status, headers, body = response.status, dict(response.headers), response.read()
except urllib.error.HTTPError as error:
    status, headers, body = error.code, dict(error.headers), error.read()
print(json.dumps({'status': status, 'headers': headers, 'body': body.decode('utf-8', 'replace')}))
