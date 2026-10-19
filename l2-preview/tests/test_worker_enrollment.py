import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gateway.worker_enrollment import WorkerEnrollment
from gateway.resident_fleet import ResidentFleet
from gateway.inference_pilot import InferencePilot, PilotError, BoundedPilotServer


class TestEnrollment(unittest.TestCase):
    def fleet(self):
        return ResidentFleet({'agent-model': {'rate_kcu_per_1k': '2.8', 'supports_tool_calls': True}}, {})

    def test_single_use_stable_retry_expiry_and_restart(self):
        with tempfile.TemporaryDirectory() as td:
            now = [1000]
            registry = WorkerEnrollment(Path(td) / 'join.sqlite3', 'https://compute.example.invalid', ['agent-model'], clock=lambda: now[0])
            fleet = self.fleet()
            invite = registry.issue('rig-1', ['agent-model'], ttl=60)
            request = {'code': invite['invitation_code'], 'client_nonce': 'n' * 48}
            profile = registry.redeem(request, fleet)
            self.assertEqual(profile, registry.redeem(request, fleet))
            self.assertFalse(profile['mainnet_funds_enabled'])
            self.assertEqual(fleet.authenticate(profile['token']), 'rig-1')
            with self.assertRaises(PilotError): registry.redeem({**request, 'client_nonce': 'x' * 48}, fleet)
            new = self.fleet(); registry.restore_workers(new)
            self.assertEqual(new.authenticate(profile['token']), 'rig-1')
            self.assertEqual(new.catalog()['data'][0]['status'], 'offline')
            expired = registry.issue('rig-2', ['agent-model'], ttl=60)
            now[0] += 61
            with self.assertRaises(PilotError): registry.redeem({'code': expired['invitation_code'], 'client_nonce': 'y' * 48}, fleet)
            self.assertNotIn('rig-2', fleet.workers)
            with self.assertRaises(ValueError): registry.issue('bad', ['arbitrary-model'])

    def test_http_enrollment_registers_real_session_and_keeps_agent_routes_private(self):
        with tempfile.TemporaryDirectory() as td:
            fleet = self.fleet(); pilot = InferencePilot(Path(td) / 'ledger', fleet)
            pilot.enrollment = WorkerEnrollment(Path(td) / 'join.sqlite3', 'https://compute.example.invalid', fleet.models)
            invite = pilot.enrollment.issue('rig', ['agent-model'])
            server = pilot.make_server()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                data = json.dumps({'code': invite['invitation_code'], 'client_nonce': 'n' * 48}).encode()
                with opener.open(urllib.request.Request(base + '/v1/join/redeem', data, {'Content-Type': 'application/json'})) as response:
                    profile = json.load(response)
                data = json.dumps({'ready_models': ['agent-model']}).encode()
                with opener.open(urllib.request.Request(base + '/v1/workers/register', data, {'Authorization': 'Bearer ' + profile['token']})) as response:
                    self.assertTrue(json.load(response)['session'])
                with self.assertRaises(urllib.error.HTTPError) as error: opener.open(base + '/v1/models')
                self.assertEqual(error.exception.code, 401)
                with opener.open(base + '/health') as response: self.assertFalse(json.load(response)['mainnet_funds_enabled'])
            finally: server.shutdown(); server.server_close(); pilot.close()

    def test_full_fleet_does_not_consume_invitation_or_poison_restart(self):
        with tempfile.TemporaryDirectory() as td:
            registry = WorkerEnrollment(Path(td) / 'join.sqlite3', 'https://compute.example.invalid', ['agent-model'])
            fleet = self.fleet()
            for i in range(256): fleet.add_worker('static-'+str(i), str(i).zfill(48), ['agent-model'])
            invite = registry.issue('new-rig', ['agent-model'])
            request = {'code': invite['invitation_code'], 'client_nonce': 'n' * 48}
            with self.assertRaises(PilotError) as error: registry.redeem(request, fleet)
            self.assertEqual(error.exception.status, 503)
            restored = self.fleet(); registry.restore_workers(restored)
            self.assertFalse(restored.workers)
            fleet.workers.pop('static-0'); fleet.queues.pop('static-0')
            profile = registry.redeem(request, fleet)
            registry.restore_workers(restored)
            self.assertEqual(restored.authenticate(profile['token']), 'new-rig')

    def test_admission_bounds_slow_connections_and_recovers(self):
        from http.server import BaseHTTPRequestHandler
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
        server = BoundedPilotServer(('127.0.0.1', 0), Handler, max_connections=2)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        held = []
        try:
            for _ in range(2):
                connection = socket.create_connection(server.server_address, timeout=2)
                connection.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n')
                held.append(connection)
            for _ in range(100):
                if server.connections._value == 0: break
                time.sleep(.005)
            extra = socket.create_connection(server.server_address, timeout=2)
            try: self.assertIn(b'503 Service Unavailable', extra.recv(1024))
            finally: extra.close()
            for connection in held: connection.close()
            for _ in range(100):
                if server.connections._value == 2: break
                time.sleep(.005)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:' + str(server.server_port)) as response: self.assertEqual(response.status, 200)
        finally:
            for connection in held: connection.close()
            server.shutdown(); server.server_close()


if __name__ == '__main__': unittest.main()
