#!/usr/bin/env python3
"""Small SSH-side stdio client for one native MCP HTTP service over a private UDS.

No tool implementation, retry or heavy backend is started in this process.
Each client owns its MCP session. Unknown calls are never replayed.
"""
import argparse
import hashlib
import concurrent.futures
import http.client
import json
import socket
import sys
import threading
import uuid
import os


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout=7200):
        super().__init__('127.0.0.1', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def fingerprint(paths):
    records = []
    for path in paths:
        with open(path, 'rb') as stream:
            records.append([path, hashlib.file_digest(stream, 'sha256').hexdigest()])
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


class Bridge:
    def __init__(self, path, emit, catalog=None):
        self.path, self.emit = path, emit
        self.session = None
        self.initial = None
        self.protocol = '2025-03-26'
        self.lock = threading.Lock()
        self.catalog = catalog
        self.virtual = False
        self.agent=os.environ.get('OPENBOT_MCP_AGENT_ID','operator')
        self.connector=uuid.uuid4().hex

    def post(self, message, emit=True):
        connection = UnixHTTP(self.path)
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': self.protocol, 'Host': '127.0.0.1:80',
                   'X-OpenBot-Agent':self.agent,'X-OpenBot-Connector':self.connector,'X-OpenBot-Call':uuid.uuid4().hex}
        if self.session:
            headers['MCP-Session-Id'] = self.session
        try:
            connection.request('POST', '/mcp', json.dumps(message).encode(), headers)
            response = connection.getresponse()
            if response.status == 404:
                self.session = None
                raise RuntimeError('MCP session expired; reconcile the previous operation, then call again')
            if response.status not in (200, 202, 204):
                raise RuntimeError('MCP HTTP request rejected (' + str(response.status) + ')')
            if response.getheader('Mcp-Session-Id'):
                self.session = response.getheader('Mcp-Session-Id')
            if response.status in (202, 204):
                response.read()
                return
            if 'text/event-stream' in response.getheader('Content-Type', ''):
                parts = []
                for line in response:
                    line = line.decode().rstrip('\r\n')
                    if line.startswith('data:'):
                        parts.append(line[5:].lstrip())
                    elif not line and parts:
                        result = json.loads('\n'.join(parts)); parts = []
                        if emit:
                            self.emit(result)
                        if result.get('id') == message.get('id') and 'method' not in result:
                            return
            else:
                raw = response.read()
                if raw:
                    result = json.loads(raw)
                    if message.get('method') == 'initialize':
                        self.protocol = result.get('result', {}).get('protocolVersion', self.protocol)
                    if emit:
                        self.emit(result)
        finally:
            connection.close()

    def handle(self, message):
        try:
            method = message.get('method')
            if self.catalog:
                if method == 'initialize':
                    self.initial = message.get('params', {})
                    self.protocol = self.initial.get('protocolVersion', self.protocol)
                    if self.protocol in self.catalog.get('protocols', []):
                        result = dict(self.catalog['initialize'], protocolVersion=self.protocol)
                        self.virtual = True
                        self.emit({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
                        return
                if self.virtual and method == 'notifications/initialized':
                    return
                if self.virtual and method == 'ping':
                    self.emit({'jsonrpc': '2.0', 'id': message['id'], 'result': {}})
                    return
                if self.virtual and method in self.catalog.get('discovery', {}) and not message.get('params', {}).get('cursor'):
                    self.emit({'jsonrpc': '2.0', 'id': message['id'], **self.catalog['discovery'][method]})
                    return
            if message.get('method') == 'initialize':
                self.initial = message.get('params', {})
                self.protocol = self.initial.get('protocolVersion', self.protocol)
            elif self.initial and method != 'notifications/cancelled':
                with self.lock:
                    if self.session and method == 'tools/call':
                        try:
                            self.post({'jsonrpc': '2.0', 'id': 'openbot-session-check', 'method': 'ping'}, emit=False)
                        except RuntimeError:
                            if self.session is not None:
                                raise
                    if self.session is None:
                        self.post({'jsonrpc': '2.0', 'id': 'openbot-reconnect',
                                   'method': 'initialize', 'params': self.initial}, emit=False)
                        self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, emit=False)
            self.post(message)
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
            if 'id' in message:
                self.emit({'jsonrpc': '2.0', 'id': message['id'], 'error': {
                    'code': -32000, 'message': 'MCP transport unavailable; operation was NOT retried. '
                    'Reconcile any write before retrying. ' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__)}})

    def close(self):
        if not self.session:
            return
        connection = UnixHTTP(self.path, timeout=10)
        try:
            connection.request('DELETE', '/mcp', headers={'MCP-Session-Id': self.session,
                               'MCP-Protocol-Version': self.protocol, 'Host': '127.0.0.1:80'})
            connection.getresponse().read()
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--catalog')
    args = parser.parse_args()
    output_lock = threading.Lock()
    def emit(message):
        with output_lock:
            sys.stdout.write(json.dumps(message, separators=(',', ':')) + '\n')
            sys.stdout.flush()
    catalog = None
    if args.catalog:
        try:
            from pathlib import Path
            candidate = json.loads(Path(args.catalog).read_text())
            if candidate['fingerprint'] == fingerprint(candidate['watch']):
                catalog = candidate
        except (OSError, ValueError, KeyError):
            pass  # Changed source: use real discovery, never stale tools.
    bridge = Bridge(args.socket, emit, catalog)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for line in sys.stdin:
                message = json.loads(line)
                if message.get('method') in ('initialize', 'notifications/initialized'):
                    bridge.handle(message)
                else:
                    pool.submit(bridge.handle, message)
    finally:
        bridge.close()


if __name__ == '__main__':
    main()
