"""Desktop connection to a miner-owned model with explicit mining pause policy."""
import argparse
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import hashlib
import secrets
from urllib.parse import urlsplit

from worker.resident_worker import ResidentWorker, inventory, private_json, RejectRedirects


def redeem_invitation(profile, directory):
    """Use a durable nonce so a lost HTTPS response never consumes another invite."""
    import urllib.request
    origin, code = profile['gateway_url'].rstrip('/'), profile['invitation_code']
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invitation requires a trusted HTTPS origin')
    if not isinstance(code, str) or not 32 <= len(code) <= 128:
        raise ValueError('Invalid invitation code')
    directory = Path(directory)
    digest = hashlib.sha256((origin + '\n' + code).encode()).hexdigest()
    pending = directory / ('join-' + digest + '.json')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        save_new({'client_nonce': secrets.token_urlsafe(32)}, pending)
    except FileExistsError:
        pass
    nonce = private_json(pending)['client_nonce']
    data = json.dumps({'code': code, 'client_nonce': nonce}).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects())
    request = urllib.request.Request(origin + '/v1/join/redeem', data, {'Content-Type': 'application/json'})
    with opener.open(request, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Join response exceeded the limit')
    result = json.loads(raw)
    if result.get('gateway_url') != origin or not isinstance(result.get('token'), str) or len(result['token']) < 32:
        raise ValueError('Invalid connection profile')
    return result


def local_models(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use a localhost model endpoint, for example http://127.0.0.1:8000/v1')
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirects())
    with opener.open(url.rstrip('/') + '/models', timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Model list exceeded the limit')
    data = json.loads(raw)
    models = [m['id'] for m in data['data'] if isinstance(m, dict) and isinstance(m.get('id'), str) and 0 < len(m['id']) <= 200]
    if not models:
        raise ValueError('No loaded models found. Start your chosen model service first.')
    return list(dict.fromkeys(models))


def make_config(values, path, previous=None):
    mode = values.get('mode', 'Pause Pearl during requests')
    floor = float(values['floor'] or '0')
    if not math.isfinite(floor) or floor < 0 or (mode == 'Run alongside Pearl' and floor == 0):
        raise ValueError('Enter a positive Pearl hashrate floor from your own baseline (TH/s).')
    grace = float(values.get('grace', '0'))
    if not math.isfinite(grace) or not 0 <= grace <= 600:
        raise ValueError('Temporary mining-rate drops may be allowed for 0 to 600 seconds; 0 is strict.')
    reasoning = values.get('reasoning', 'Backend default')
    if reasoning not in ('Backend default', 'Disabled', 'Enabled'):
        raise ValueError('Choose a supported reasoning setting.')
    if mode not in ('Pause Pearl during requests', 'Run alongside Pearl'):
        raise ValueError('Choose a mining mode.')
    token = values['token'].strip()
    if len(token) < 32 or token.startswith('REPLACE_'):
        raise ValueError('Import your operator-issued connection profile or enter a dedicated Worker token.')
    if not values['model'].strip():
        raise ValueError('Detect and select a loaded model first.')
    parsed = urlsplit(values['pearl'])
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password:
        raise ValueError('Use the local Pearl API endpoint.')
    config = {'gateway_url': values['gateway'].strip(), 'backend_url': values['backend'].strip(),
              'token': token, 'model': values.get('offered_model', values['model']).strip(),
              'backend_model': values['model'].strip(),
              'gpu_indices': [int(x.strip()) for x in values['gpus'].split(',')],
              'miner_process_name': values['miner'].strip(), 'pearl_api_url': values['pearl'].strip(),
              'min_pearl_hashrate_hs': floor * 1e12, 'hashrate_grace_seconds': grace,
              'min_free_mib': 512, 'max_temperature_c': 80,
              'inference_timeout': 120, 'status_file': str(path.with_name('status.json'))}
    previous = previous or {}
    for key in ('miner_launch', 'miner_service', 'model_launch', 'inference_recommendation'):
        if key in previous: config[key] = previous[key]
    selected=config.get('inference_recommendation',{})
    selected_alias=selected.get('backend_alias',selected.get('model',{}).get('id'))
    if selected_alias and selected_alias!=config['backend_model']:
        config.pop('inference_recommendation',None);config.pop('model_launch',None)
    for key in ('min_free_mib', 'max_temperature_c'):
        if key in previous: config[key] = previous[key]
    same_backend = (previous.get('backend_url') == config['backend_url'] and
                    previous.get('backend_model', previous.get('model')) == config['backend_model'])
    options = dict(previous.get('chat_template_kwargs', {})) if same_backend else {}
    recommendation=config.get('inference_recommendation',{})
    if recommendation.get('reference_qualification'):
        for key,value in recommendation.get('chat_template_kwargs',{}).items():options.setdefault(key,value)
    if same_backend and 'inference_timeout' in previous:
        config['inference_timeout'] = previous['inference_timeout']
    options.pop('enable_thinking', None)
    if reasoning != 'Backend default': options['enable_thinking'] = reasoning == 'Enabled'
    if options: config['chat_template_kwargs'] = options
    if recommendation.get('reference_qualification') and any(
            options.get(key)!=value for key,value in recommendation.get('chat_template_kwargs',{}).items()):
        recommendation=dict(recommendation);recommendation.pop('reference_qualification',None)
        recommendation['acceptance']='manual_options_differ_from_reference'
        config['inference_recommendation']=recommendation
    if mode == 'Pause Pearl during requests':
        config.update(mining_mode='pause_on_request',inference_timeout=90,preemption_max_seconds=120,
                      mining_lease_file=str(path.with_name('mining-lease.json')))
    ResidentWorker(config)
    return config


def save_new(config, path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke-check', action='store_true', help='Open the native UI briefly without network or GPU calls')
    parser.add_argument('--advisor-smoke-check', action='store_true', help='Open the local recommendation picker without scanning')
    args = parser.parse_args()
    smoke = args.smoke_check or args.advisor_smoke_check
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    root = tk.Tk()
    root.title('BTC-K Compute Connect — Preview')
    root.geometry('920x880')
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    ttk.Label(frame, text='Connect your GPU to BTC-K L2', font=('', 17, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
    ttk.Label(frame, text='Run your chosen model locally, import a connection profile, then connect.\nDevelopment credits only. Your mining program remains under your control.').grid(row=1, column=0, columnspan=2, sticky='w', pady=12)
    defaults = {'gateway': '', 'token': '', 'backend': 'http://127.0.0.1:8000/v1', 'model': '',
                'reasoning': 'Backend default', 'mode': 'Pause Pearl during requests', 'gpus': '0',
                'miner': 'SRBMiner-MULTI.exe' if os.name == 'nt' else 'SRBMiner-MULTI',
                'pearl': 'http://127.0.0.1:21550', 'floor': '', 'grace': '180'}
    labels = {'gateway': 'Gateway HTTPS address', 'token': 'Worker access token', 'backend': 'Local model endpoint',
              'model': 'Loaded model', 'gpus': 'GPU indices (comma separated)', 'miner': 'Mining program filename',
              'pearl': 'Local Pearl API', 'floor': 'Mining recovery baseline (TH/s)',
              'grace': 'Temporary rate drop allowance (seconds)',
              'reasoning': 'Reasoning (if backend supports it)', 'mode':'Mining mode'}
    variables, widgets = {}, []
    combo = None
    for row, (key, default) in enumerate(defaults.items(), 2):
        variables[key] = tk.StringVar(value=default)
        ttk.Label(frame, text=labels[key]).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 16))
        if key == 'model':
            entry = combo = ttk.Combobox(frame, textvariable=variables[key], state='readonly')
        elif key == 'reasoning':
            entry = ttk.Combobox(frame, textvariable=variables[key], state='readonly',
                                 values=('Backend default', 'Disabled', 'Enabled'))
        elif key == 'mode':
            entry = ttk.Combobox(frame, textvariable=variables[key], state='readonly',
                                 values=('Pause Pearl during requests', 'Run alongside Pearl'))
        else:
            entry = ttk.Entry(frame, textvariable=variables[key], show='*' if key == 'token' else '')
        entry.grid(row=row, column=1, sticky='ew')
        widgets.append(entry)
    state = {'worker': None, 'thread': None, 'busy': False, 'closing': False, 'allowed_models': [], 'launch_settings': {},
             'connected_at':None,'ever_ready':False}
    events = queue.Queue()
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.config'))) / 'BTC-K' / 'compute-connect'
    path = base / 'worker.json'
    if not smoke and path.exists():
        try:
            saved = private_json(path)
            state['launch_settings'] = {k:saved[k] for k in ('miner_launch','miner_service','model_launch','inference_recommendation') if k in saved}
            mapping = {'gateway': 'gateway_url', 'backend': 'backend_url', 'token': 'token',
                       'model': 'backend_model', 'miner': 'miner_process_name', 'pearl': 'pearl_api_url'}
            for field, key in mapping.items():
                variables[field].set(saved.get(key, saved.get('model', '')))
            variables['floor'].set(str(saved['min_pearl_hashrate_hs'] / 1e12))
            variables['grace'].set(str(saved.get('hashrate_grace_seconds', 0)))
            variables['mode'].set('Pause Pearl during requests' if saved.get('mining_mode')=='pause_on_request' else 'Run alongside Pearl')
            thinking = saved.get('chat_template_kwargs', {}).get('enable_thinking')
            variables['reasoning'].set('Enabled' if thinking is True else 'Disabled' if thinking is False else 'Backend default')
            variables['gpus'].set(','.join(str(i) for i in saved['gpu_indices']))
            combo['values'] = [saved.get('backend_model', saved['model'])]
            state['allowed_models'] = [saved['model']]
        except Exception:
            messagebox.showerror('Saved settings', 'Saved configuration could not be loaded. Back it up before creating new settings.')
    status = tk.StringVar(value='Not connected. Import a profile issued by the test network operator.')
    form_end = 2 + len(defaults)
    ttk.Label(frame, textvariable=status, wraplength=750).grid(row=form_end+1, column=0, columnspan=2, sticky='w', pady=18)

    def background(function, event):
        if state['busy']:
            return
        state['busy'] = True
        def run():
            try: events.put((event, function()))
            except Exception as error:
                # No raw requests, configuration or credential-bearing tracebacks in UI logs.
                events.put(('error', type(error).__name__ + ': check endpoint, profile and local service.'))
        threading.Thread(target=run, daemon=True).start()

    def import_profile():
        if state['worker']:
            return
        filename = filedialog.askopenfilename(title='Import private connection profile', filetypes=[('JSON connection profile', '*.json')])
        if not filename:
            return
        try:
            profile = private_json(filename)
            if profile.get('invitation_code'):
                status.set('Redeeming invitation over HTTPS…')
                background(lambda: redeem_invitation(profile, base), 'profile')
            else:
                apply_profile(profile)
        except Exception:
            messagebox.showerror('Invalid profile', 'Choose a private JSON profile containing gateway_url and token.')

    def apply_profile(profile):
        variables['gateway'].set(profile['gateway_url'])
        variables['token'].set(profile['token'])
        state['launch_settings'].update({k:profile[k] for k in ('miner_launch','miner_service','model_launch','inference_recommendation') if k in profile})
        state['allowed_models'] = profile.get('allowed_models', [profile['model']] if profile.get('model') else [])
        if profile.get('model'):
            combo['values'] = [profile['model']]
            variables['model'].set(profile['model'])
        status.set('Profile imported. Detect your local model and set your mining floor before connecting.')

    def detect():
        if state['worker']:
            return
        url = variables['backend'].get().strip()
        background(lambda: {'models': local_models(url), 'hardware': inventory()}, 'detected')

    def advisor():
        if state['worker'] or state['busy']:return
        from worker.advisor_dialog import open_advisor
        def apply(settings):
            for key in ('model_launch','inference_recommendation'):state['launch_settings'][key]=settings[key]
            variables['model'].set(settings['backend_model']);combo['values']=[settings['backend_model']]
            variables['gpus'].set(','.join(map(str,settings['gpu_indices'])))
            thinking=settings['inference_recommendation'].get('chat_template_kwargs',{}).get('enable_thinking')
            if thinking is not None:variables['reasoning'].set('Enabled' if thinking else 'Disabled')
            status.set('Local launch settings selected. Connect saves them and starts the model if absent; an existing model is kept running. Estimated capacity is not a benchmark.')
        open_advisor(root,variables['backend'].get().strip(),base/'model.log',apply)

    def billing():
        from worker.service_info import BILLING_TEXT
        window=tk.Toplevel(root);window.title('计费说明 / Billing');window.geometry('820x700')
        text=tk.Text(window,wrap='word',padx=16,pady=16);text.pack(fill='both',expand=True)
        rates=state['worker'].pricing.get('rates',{}) if state['worker'] else {}
        current='当前网关模型费率：\n'+'\n'.join(f'{m}: {r} KCU / 千计费单位' for m,r in rates.items()) if rates else '连接后显示网关当前模型 KCU 费率。'
        text.insert('end',current+'\n\n'+BILLING_TEXT);text.configure(state='disabled')

    def connect():
        if state['worker'] or state['busy']:
            return
        owner = None
        try:
            values = {k: v.get() for k, v in variables.items()}
            if len(state['allowed_models']) == 1:
                values['offered_model'] = state['allowed_models'][0]
            elif state['allowed_models'] and values['model'] not in state['allowed_models']:
                raise ValueError('Choose an approved model or import a profile for one model alias.')
            previous = {**(private_json(path) if path.exists() else {}), **state['launch_settings']}
            config = make_config(values, path, previous)
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            worker = ResidentWorker(config)
            # OS-owned lock prevents two desktop instances from sharing a Worker session.
            # The lock is released automatically even if the app or computer crashes.
            owner = open(base / 'client.lock', 'a+b')
            try:
                owner.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    if not owner.read(1):
                        owner.write(b'0'); owner.flush()
                    owner.seek(0)
                    msvcrt.locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                owner.close()
                raise ValueError('Another Compute Connect instance is already connected.') from None
            if path.exists() and private_json(path) != config:
                # Connect is an explicit settings change; preserve one private backup.
                previous = path.with_name('worker.previous.json')
                old = private_json(path)
                backup = path.with_name('worker.previous.new')
                save_new(old, backup)
                os.replace(backup, previous)
                pending = path.with_name('worker.new')
                save_new(config, pending)
                os.replace(pending, path)
            elif not path.exists():
                save_new(config, path)
        except Exception as error:
            if owner is not None:
                owner.close()
            messagebox.showerror('Cannot connect', str(error))
            return
        state['worker'] = worker
        state['connected_at']=time.monotonic();state['ever_ready']=False
        def run():
            try:
                if config.get('mining_mode')=='pause_on_request' and config.get('model_launch'):
                    from worker.compute_node import run_node
                    run_node(path,worker=worker)
                else:worker.run()
            except Exception: events.put(('error', 'Connection rejected. Check your dedicated Worker profile.'))
            finally:
                owner.close()
                events.put(('stopped', None))
        state['thread'] = threading.Thread(target=run, daemon=True)
        state['thread'].start()
        for widget in widgets: widget.configure(state='disabled')
        status.set('Connecting. Checking GPU, model and mining protection before accepting work…')

    def stop():
        if state['worker']:
            state['worker'].stop.set()
            status.set('Disconnecting after the current request returns. Mining and model services stay running.')

    def tick():
        while not events.empty():
            event, data = events.get_nowait()
            state['busy'] = False
            if event == 'detected':
                combo['values'] = data['models']
                variables['model'].set(data['models'][0])
                cards = ', '.join(str(g['index']) + ': ' + g['name'] for g in data['hardware']['gpus'])
                status.set('Detected ' + cards + '. Select your model; enter your own mining floor.')
            elif event == 'stopped':
                state['worker'] = None
                for widget in widgets: widget.configure(state='readonly' if isinstance(widget, ttk.Combobox) else 'normal')
                status.set('Disconnected.')
                if state['closing']:
                    root.destroy()
                    return
            elif event == 'profile':
                apply_profile(data)
            elif event == 'error':
                status.set(data)
        worker = state['worker']
        if worker and not worker.stop.is_set():
            from worker.service_info import verification_label
            allowed=worker.last_ready and worker.verification_status not in ('pending','unavailable')
            if worker.last_ready:state['ever_ready']=True
            primary='Accepting work' if allowed else 'Not accepting work: '+worker.last_gate
            if worker.last_ready and worker.verification_status=='pending':primary='等待服务验证'
            elif (not state['ever_ready'] and state['launch_settings'].get('model_launch') and
                  worker.last_gate in ('backend_or_probe_unavailable','backend_model_missing')):
                elapsed=int(time.monotonic()-state['connected_at'])
                primary=f'等待本地模型就绪，已等待 {elapsed//60}分{elapsed%60}秒'
            status.set(primary +
                       ' | '+verification_label(worker.verification_status)+
                       ' | Calls this session: ' + str(worker.calls) + ' | Earned: ' + str(worker.earned) + ' development SatK')
        root.after(1000, tick)

    def close():
        if state['worker']:
            state['closing'] = True
            stop()
        else: root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=form_end, column=0, columnspan=2, sticky='w', pady=15)
    for label, action in [('Import profile', import_profile), ('GPU / 模型推荐', advisor), ('Detect loaded model', detect), ('计费说明', billing), ('Connect', connect), ('Disconnect', stop)]:
        ttk.Button(buttons, text=label, command=action).pack(side='left', padx=(0, 8))
    ttk.Label(frame, text='Pause mode: mining resumes after each request; the model stays loaded.\nUse root/administrator rights when the miner requires them. Model/runtime are provided by you.\nAlongside mode uses the mining baseline and drop allowance. Settings: ' + str(path),
              wraplength=750).grid(row=form_end+2, column=0, columnspan=2, sticky='w')
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(1000, tick)
    if args.advisor_smoke_check:root.after(100,advisor)
    if smoke:root.after(1200, root.destroy)
    root.mainloop()


if __name__ == '__main__':
    main()
