"""What the gate does when the laptop misbehaves, with the real Caddy image.

Run:  uv run --no-project --with 'pytest>=8,<10' --with pyyaml python -m pytest deploy/tests/gate -v
Needs docker, helm, and (for the blackhole row) iptables + nsenter, as root or via sudo.
"""
import http.client
import json
import time

import pytest

from conftest import GATE_BODY, sh, DOCKER

DIAL_TIMEOUT = 3
PROBE_INTERVAL = 10
SHORT_HEADER_TIMEOUT = 4


def get(port, path, timeout=30):
    """One request through the gate: (status, headers, body, seconds)."""
    start = time.monotonic()
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    conn.request('GET', path)
    resp = conn.getresponse()
    body = resp.read()
    elapsed = time.monotonic() - start
    conn.close()
    return resp.status, resp, body, elapsed


def assert_fixed_answer(result):
    status, resp, body, _ = result
    assert status == 503
    assert resp.getheader('Content-Type') == 'application/json'
    assert body.decode() == GATE_BODY
    assert json.loads(body)['error']['type'] == 'local_unavailable'


def test_connection_refused(lab, caddyfile_short, tmp_path):
    fake = lab.fake('--no-listen')
    port = lab.gate(caddyfile_short, fake, tmp_path)
    result = get(port, '/v1/chat')
    assert_fixed_answer(result)
    assert result[3] < 1, result[3]


def wait_for_next_probe(fake, timeout=15):
    """Return as soon as a probe has just reached the fake (host monotonic time of noticing)."""
    seen = fake.stats()['version']
    deadline = time.monotonic() + timeout
    while fake.stats()['version'] <= seen:
        assert time.monotonic() < deadline, 'the gate stopped probing'
        time.sleep(0.02)
    return time.monotonic()


def test_syn_blackholed(lab, caddyfile_short, tmp_path):
    """The laptop was answering, then its packets stop (SYNs dropped, no reset).

    Synchronised on the probe, not on start-up timing: wait until a probe has just succeeded,
    drop the packets, and send the request at once. The next probe is ~10 s away, so the
    upstream is still marked up and the request must take the dial path and be bounded by the
    dial timeout. With Caddy's default pooled connection this took the response-header timeout
    instead (measured 4.00 s here, 180 s by default); `keepalive off` in the gate is what makes
    this pass, and it also covers the 'successful request, then gone' case."""
    fake = lab.fake()
    port = lab.gate(caddyfile_short, fake, tmp_path)
    assert get(port, '/ok')[0] == 200  # a proxied request that succeeded before the laptop went
    probed = wait_for_next_probe(fake)
    fake.blackhole()
    result = get(port, '/ok')
    answered = time.monotonic()
    print(f'blackhole answered in {result[3]:.2f}s')
    assert_fixed_answer(result)
    assert result[3] < DIAL_TIMEOUT + 1, result[3]
    assert result[3] >= DIAL_TIMEOUT - 0.5, f'{result[3]}: not bounded by the dial'
    # No probe can have failed first: the next one was not due until probed + interval.
    assert answered - probed < PROBE_INTERVAL - 2, answered - probed
    # Then the next probe fails and the upstream is marked down: immediate answers.
    time.sleep(PROBE_INTERVAL + DIAL_TIMEOUT)
    after = get(port, '/ok')
    assert_fixed_answer(after)
    assert after[3] < 0.5, after[3]


def test_accepts_and_never_answers(lab, caddyfile_short, tmp_path):
    fake = lab.fake()
    port = lab.gate(caddyfile_short, fake, tmp_path)
    result = get(port, '/hang')
    print(f'hung upstream answered in {result[3]:.2f}s')
    assert_fixed_answer(result)
    assert SHORT_HEADER_TIMEOUT - 0.5 <= result[3] < SHORT_HEADER_TIMEOUT + 1.5, result[3]
    assert fake.stats()['proxied'] == 1


def test_marked_down_by_probe_is_answered_without_a_dial(lab, caddyfile_short, tmp_path):
    fake = lab.fake('--version-status', '500')
    port = lab.gate(caddyfile_short, fake, tmp_path)
    deadline = time.monotonic() + 15
    while fake.stats()['version'] < 1:
        assert time.monotonic() < deadline, 'the gate never probed'
        time.sleep(0.1)
    time.sleep(0.5)
    before = fake.stats()
    result = get(port, '/v1/chat')
    print(f'marked-down answered in {result[3]:.3f}s')
    assert_fixed_answer(result)
    assert result[3] < 0.5, result[3]
    assert fake.stats()['proxied'] == before['proxied'] == 0, 'a request reached the upstream'


def test_stream_cut_after_bytes_is_truncated_not_replaced(lab, caddyfile_short, tmp_path):
    fake = lab.fake()
    port = lab.gate(caddyfile_short, fake, tmp_path)
    start = time.monotonic()
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    conn.request('GET', '/drop')
    resp = conn.getresponse()
    assert resp.status == 200, 'headers had already gone out; no 503 is possible'
    assert resp.getheader('Transfer-Encoding') == 'chunked'
    received = b''
    failure = None
    try:
        while True:
            piece = resp.read1(65536)
            if not piece:
                break
            received += piece
    except (http.client.IncompleteRead, ConnectionError) as exc:
        failure = exc
    elapsed = time.monotonic() - start
    print(f'cut stream: {type(failure).__name__} after {elapsed:.2f}s, {len(received)} bytes')
    assert failure is not None, 'a cut stream looked like a clean end to the client'
    assert elapsed < 3, elapsed
    assert received.count(b'\n') == 3, received
    assert GATE_BODY.encode() not in received


@pytest.mark.slow
def test_long_silence_then_two_minute_stream_is_delivered_unbuffered(lab, caddyfile_default, tmp_path):
    silence, chunks, interval = 12, 125, 1.0
    assert silence > PROBE_INTERVAL and silence > DIAL_TIMEOUT
    fake = lab.fake()
    port = lab.gate(caddyfile_default, fake, tmp_path)
    start = time.monotonic()
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    conn.request('GET', f'/stream?silence={silence}&n={chunks}&interval={interval}')
    resp = conn.getresponse()
    first_header = time.monotonic() - start
    assert resp.status == 200
    assert first_header >= silence - 0.5
    arrivals, sent, buf = [], [], b''
    while True:
        piece = resp.read1(65536)  # IncompleteRead here means the stream was cut: test fails
        if not piece:
            break
        now = time.monotonic()
        buf += piece
        while b'\n' in buf:
            line, buf = buf.split(b'\n', 1)
            arrivals.append(now)
            sent.append(json.loads(line)['sent'])
    total = time.monotonic() - start
    print(f'{len(arrivals)} chunks, first header after {first_header:.1f}s, total {total:.1f}s')
    assert len(arrivals) == chunks and buf == b''
    assert total > silence + (chunks - 1) * interval - 1
    worst = max(abs((a - arrivals[0]) - (s - sent[0])) for a, s in zip(arrivals, sent))
    print(f'worst drift between send and arrival spacing: {worst * 1000:.0f} ms')
    assert worst < 0.5, 'chunks were buffered or bunched'
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    assert min(gaps) > interval - 0.5 and max(gaps) < interval + 0.5, (min(gaps), max(gaps))
    # The fake answered the probe throughout, so the upstream was never marked down.
    assert fake.stats()['version'] >= (silence + chunks * interval) // PROBE_INTERVAL - 1


def test_gone_then_back_fails_and_recovers_within_a_probe_interval(lab, caddyfile_short, tmp_path):
    fake = lab.fake()
    port = lab.gate(caddyfile_short, fake, tmp_path)
    assert get(port, '/ok')[0] == 200

    lab.kill(fake)
    killed = time.monotonic()
    while True:
        result = get(port, '/ok', timeout=10)
        if result[0] == 503:
            break
        assert time.monotonic() - killed < PROBE_INTERVAL, 'still serving after the upstream vanished'
        time.sleep(0.25)
    assert_fixed_answer(result)
    failed_after = time.monotonic() - killed
    print(f'failed {failed_after:.2f}s after the upstream vanished')
    assert failed_after < PROBE_INTERVAL

    # Stay down across at least one probe so the gate has marked it down.
    time.sleep(PROBE_INTERVAL + 3)
    assert_fixed_answer(get(port, '/ok'))

    lab.restart_fake(fake)
    back = time.monotonic()
    while True:
        result = get(port, '/ok', timeout=10)
        if result[0] == 200:
            break
        assert time.monotonic() - back < PROBE_INTERVAL + 1, 'no recovery within one probe interval'
        time.sleep(0.25)
    print(f'recovered {time.monotonic() - back:.2f}s after the upstream returned')
    assert result[2] == b'{"ok":true}'


def test_upstream_http_error_passes_through(lab, caddyfile_short, tmp_path):
    """Documents the second case where the fixed message is not what the user sees."""
    fake = lab.fake()
    port = lab.gate(caddyfile_short, fake, tmp_path)
    status, resp, body, _ = get(port, '/error')
    assert status == 500
    assert b'the upstream' in body and GATE_BODY.encode() not in body
