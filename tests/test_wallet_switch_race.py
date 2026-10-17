"""Exercise delayed tip callbacks while a wallet password dialog runs Tk events."""
import sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'src')]
from btck_wallet_gui import WalletApp
from privacy.wallet_registry import WalletRegistry

class SwitchRaceTests(unittest.TestCase):
    def app(self,path):
        events=[]
        app=SimpleNamespace(_refreshing_wallet_list=False,busy=False,
            _password_prompt_active=False,_closing=False,_wallet_generation=4,
            _auto_sync_after_id=None,_tip_check_in_progress=True,_auto_sync_failures=0,
            wallet=object(),active_wallet_id='main',height=10,ledger_tip_hash='old',
            registry=SimpleNamespace(get=lambda _: {'path':str(path)}),
            _selected_wallet_id=lambda:'pool',_update_controls=lambda:None,
            _refresh_wallet_list=lambda *a:None,
            _cancel_auto_sync_timer=lambda:None,
            _schedule_auto_sync=lambda *a:events.append(('resume',a)),
            status_var=SimpleNamespace(set=lambda *a:None))
        app._stop_auto_sync=lambda **kw:WalletApp._stop_auto_sync(app,**kw)
        app.sync_wallet=lambda **kw:(events.append(('old_scan',kw)),setattr(app,'busy',True))
        app._open_wallet_path=lambda *a,**kw:events.append(('ignored' if app.busy else 'opened',kw))
        return app,events

    def test_inflight_tip_cannot_start_old_scan_during_password_entry(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'pool.json';p.write_text('{}')
            app,events=self.app(p)
            generation=app._wallet_generation
            def password():
                WalletApp._auto_tip_finished(app,generation,{'tip_height':11,'tip_hash':'new'})
                return 'disposable-password'
            app._ask_password=password
            WalletApp._wallet_selected(app)
            self.assertTrue(any(e[0]=='opened' for e in events),events)
            self.assertFalse(any(e[0] in ('old_scan','ignored') for e in events),events)
            self.assertFalse(app._password_prompt_active)

    def test_cancelled_switch_resumes_existing_wallet(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'pool.json';p.write_text('{}')
            app,events=self.app(p);original=app.wallet
            app._ask_password=lambda:None
            WalletApp._wallet_selected(app)
            self.assertIs(app.wallet,original)
            self.assertTrue(any(e[0]=='resume' for e in events),events)
            self.assertFalse(app._password_prompt_active)

    def test_register_preserves_wallet_imported_by_another_local_process(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);index=root/'registry.json'
            existing=WalletRegistry(index)
            existing.add(root/'main.json','Main')
            # The running application has an older in-memory registry.
            app=SimpleNamespace(registry=existing,active_wallet_id=None,
                                _refresh_wallet_list=lambda *a:None)
            WalletRegistry(index).add(root/'miner.json','Mining')
            WalletApp._register_wallet(app,root/'main.json',SimpleNamespace(name='Main'))
            current=WalletRegistry(index)
            self.assertEqual({r['label'] for r in current.records},{'Main','Mining'})

if __name__=='__main__':unittest.main()
