"""Actual local transport checks, using a deliberately bounded protocol fixture."""
import json
import socket
import socketserver
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from http_stdio import Bridge


class UnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = True


class SocketTransportTests(unittest.TestCase):
    def test_sessions_and_lost_write_acknowledgment(self):
        state = {'sessions': [], 'writes': 0}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method = message['method']
                session = self.headers.get('MCP-Session-Id')
                if method == 'initialize':
                    session = 'fixture-session-' + str(len(state['sessions']))
                    state['sessions'].append(session)
                    result = {'protocolVersion': '2025-03-26', 'capabilities': {}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
                elif method == 'tools/call':
                    state['writes'] += 1
                    if message['params'].get('lose_ack'):
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    result = {'session': session, 'writes': state['writes']}
                elif method == 'ping':
                    result = {}
                else:
                    self.send_response(202)
                    self.end_headers()
                    return
                raw = json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Mcp-Session-Id', session)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'fixture.sock')
            server = UnixServer(path, Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                outputs = [[], []]
                bridges = [Bridge(path, outputs[i].append) for i in range(2)]
                for bridge in bridges:
                    bridge.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}})
                    bridge.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                self.assertNotEqual(bridges[0].session, bridges[1].session)
                bridges[0].handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'write', 'lose_ack': True}})
                self.assertEqual(state['writes'], 1)
                self.assertIn('NOT retried', outputs[0][-1]['error']['message'])
                bridges[1].handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'write'}})
                self.assertEqual(state['writes'], 2)
                self.assertEqual(outputs[1][-1]['result']['session'], bridges[1].session)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)
