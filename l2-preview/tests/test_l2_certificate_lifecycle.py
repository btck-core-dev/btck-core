import importlib.util
from pathlib import Path
import sqlite3
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('l2_certificate', ROOT/'deploy/l2-preview/issue_certificate.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CertificateLifecycle(unittest.TestCase):
    def run_case(self, *, valid=False, idle=True, ca_failure=False, dns_ready=True):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cert=root/'cert.crt';cert.touch();calls=[]
            def run(args, **kwargs):
                calls.append(args)
                if args[0]=='openssl':return subprocess.CompletedProcess(args, 0 if valid else 1)
                if args[0]=='lego' and ca_failure:raise subprocess.CalledProcessError(1,args)
                return subprocess.CompletedProcess(args,0)
            with patch.multiple(m,STORE=root/'store',CERTIFICATE=cert,DRAIN=root/'run/draining'), \
                 patch.object(m.os,'geteuid',return_value=0), \
                 patch.object(m.socket,'getaddrinfo',return_value=[(2,1,6,'',('47.131.226.181' if dns_ready else '192.0.2.1',443))]), \
                 patch.object(m.subprocess,'run',side_effect=run),patch.object(m,'wait_for_idle',return_value=idle) as wait:
                if not dns_ready:
                    with self.assertRaises(SystemExit):m.issue()
                elif ca_failure:
                    with self.assertRaises(subprocess.CalledProcessError):m.issue()
                else:m.issue()
                self.assertFalse(m.DRAIN.exists())
                if valid or not dns_ready:wait.assert_not_called()
            return calls

    def test_valid_certificate_does_not_interrupt_edge(self):
        self.assertTrue(all(c[0]=='openssl' for c in self.run_case(valid=True)))

    def test_wrong_dns_cannot_request_or_stop(self):
        self.assertEqual(self.run_case(dns_ready=False),[])

    def test_busy_gateway_defers_without_stopping(self):
        calls=self.run_case(idle=False)
        self.assertFalse(any(c[0]=='lego' or c[:2]==['systemctl','stop'] for c in calls))

    def test_ca_failure_restarts_edge_and_removes_admission_flag(self):
        calls=self.run_case(ca_failure=True)
        self.assertIn(['systemctl','stop','btck-l2-edge.service'],calls)
        self.assertEqual(calls[-1],['systemctl','start','btck-l2-edge.service'])

    def test_renewal_uses_443_after_drain_and_restarts_edge(self):
        calls=self.run_case()
        self.assertEqual([c[1] for c in calls if c[0]=='systemctl'],['is-active','stop','start'])
        command=next(c for c in calls if c[0]=='lego')
        self.assertIn('renew',command);self.assertIn(':443',command)

    def test_quiet_window_restarts_when_work_arrives(self):
        now=[0]
        def sleep(seconds):now[0]+=seconds
        def tasks():return int(now[0] in (0,3))
        with patch.object(m.time,'monotonic',side_effect=lambda:now[0]),patch.object(m.time,'sleep',side_effect=sleep),patch.object(m,'active_customer_tasks',side_effect=tasks):
            self.assertTrue(m.wait_for_idle(timeout=20,quiet=3))
            self.assertEqual(now[0],7)

    def test_ledger_query_is_read_only_and_counts_only_active_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);db=base/'pilot.sqlite3';config=base/'config.json'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE tasks(status TEXT)')
                c.executemany('INSERT INTO tasks VALUES(?)', [('IN_FLIGHT',),('UNKNOWN',),('SETTLED',)])
            config.write_text(json.dumps({'data_dir':td}));before=db.read_bytes()
            with patch.object(m,'GATEWAY_CONFIG',config):self.assertEqual(m.active_customer_tasks(),1)
            self.assertEqual(before,db.read_bytes())


if __name__=='__main__':unittest.main()
