"""Native local model recommendation picker; UI actions run no inference."""
import queue
import threading
from worker.inference_advisor import gpu_snapshot, scan_models, recommendations, estimate, launch_entry, runtime_metadata


def open_advisor(root, backend_url, log, apply):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    window = tk.Toplevel(root)
    window.title('模型与显存推荐 / Model recommendations')
    window.geometry('880x760')
    frame = ttk.Frame(window, padding=16); frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    state = {'models': [], 'cards': [], 'busy': False, 'runtime':None, 'runtime_python':None}
    events = queue.Queue()
    kv_options={'自动':'auto','FP8':'fp8','4bit':'turboquant_4bit_nc'}
    prefix_options={'自动':'auto','开启':'enabled','关闭':'disabled'}
    speculation_options={'关闭':'off','轻量猜字':'ngram'}
    values = {k: tk.StringVar(value=v) for k,v in {
        'directory':'', 'python':'', 'gpus':'0', 'model':'', 'context':'8192',
        'kv':'自动', 'prefill':'256', 'prefix':'自动', 'speculation':'关闭'}.items()}
    labels = {'directory':'本地模型或模型库目录', 'python':'已安装 vLLM 的 Python 程序',
              'gpus':'使用显卡编号（逗号分隔）', 'model':'本地模型', 'context':'上下文（输入＋输出）',
              'kv':'KV 缓存精度', 'prefill':'分块预填充 token 数', 'prefix':'前缀缓存', 'speculation':'猜字 / 推测解码'}
    choices = {'model':[], 'context':['4096','8192','16384','32768','65536','131072'],
               'kv':list(kv_options), 'prefill':['128','256','512','1024'],
               'prefix':list(prefix_options),'speculation':list(speculation_options)}
    boxes = {}
    for row,key in enumerate(values):
        ttk.Label(frame,text=labels[key]).grid(row=row,column=0,sticky='w',pady=5)
        widget = (ttk.Combobox(frame,textvariable=values[key],values=choices[key],state='readonly')
                  if key in choices else ttk.Entry(frame,textvariable=values[key]))
        widget.grid(row=row,column=1,sticky='ew',padx=8); boxes[key]=widget
        if key in ('directory','python'):
            def choose(k=key):
                path = filedialog.askdirectory(parent=window) if k=='directory' else filedialog.askopenfilename(parent=window)
                if path: values[k].set(path)
            ttk.Button(frame,text='选择',command=choose).grid(row=row,column=2)
    text = tk.Text(frame,height=19,wrap='word');text.grid(row=9,column=0,columnspan=3,sticky='nsew',pady=12)
    frame.rowconfigure(9,weight=1)
    def show(message):
        text.configure(state='normal');text.delete('1.0','end');text.insert('end',message);text.configure(state='disabled')
    def selected_cards():
        indices=[int(x.strip()) for x in values['gpus'].get().split(',')]
        if len(set(indices))!=len(indices) or not indices: raise ValueError('请选择不同的显卡编号')
        cards={c['index']:c for c in state['cards']}
        return [cards[i] for i in indices]
    def plan():
        model=next(m for m in state['models'] if m['id']==values['model'].get())
        runtime=state['runtime'] if state['runtime_python']==values['python'].get() else None
        return estimate(model,selected_cards(),int(values['context'].get()),kv_options[values['kv'].get()],
                        int(values['prefill'].get()),runtime,prefix_options[values['prefix'].get()],speculation_options[values['speculation'].get()])
    def describe(p):
        lines=['检测值：当前占用（包括 Pearl 和已加载模型）均不会被当作可用显存。']
        for c in selected_cards():
            lines.append(f"GPU {c['index']} {c['name']}：总 {c['total_mib']/1024:.1f} GiB，剩余 {c['free_mib']/1024:.1f} GiB，{c['temperature_c']}°C")
        lines += [f"\n选择：{p['model']['id']} / TP={p['tensor_parallel_size']} / 上下文 {p['context']}",
                  f"估算每卡需求 {p['estimated_required_per_gpu_mib']} MiB（含运行余量）",
                  '显存估算：'+('可容纳；本机尚待验证' if p['fits_estimate'] else '不足或无法估算'),
                  '\n分块预填充开启；前缀缓存'+('开启' if p['prefix_caching'] else '关闭（兼容模式）')+'。auto KV 使用后端默认精度。',
                  'FP8/4bit KV、猜字需要后端兼容性与质量实测；它们不是保证提速的开关。',
                  '模型仅从你选择的本地目录读取，不下载。工具模板检测不等于工具调用验收。',
                  '已运行模型会被接管；这里的启动选项在下次启动模型时生效，不会热改或杀掉现有模型。']
        if p['reasons']:lines+=['\n待确认：']+p['reasons']
        if p.get('reference_qualification'):
            m=p['reference_qualification']['measurements']
            lines+=['\n同配置参考机实测（不代表本机已经验收）：',
                    f"短请求首字 {m.get('short_ttft_seconds','—')} 秒；长文输入 {m.get('long_input_tokens','—')} token，首字 {m.get('long_ttft_seconds','—')} 秒。",
                    '工具回合：'+('通过' if m.get('tool_loop_passed') else '待确认')]
            if p.get('chat_template_kwargs',{}).get('enable_thinking') is False:lines.append('这组参考实测关闭思考模式；采用推荐会同时应用该选项。')
        show('\n'.join(lines))
    def inspect():
        if state['busy']:return
        directory=values['directory'].get()
        python=values['python'].get()
        state['busy']=True;show('正在检测本地显卡和模型元数据…')
        def run():
            try:events.put(('ok',(gpu_snapshot(),scan_models(directory),runtime_metadata(python),python)))
            except Exception as e:events.put(('error',str(e)))
        threading.Thread(target=run,daemon=True).start()
    def recommend():
        try:
            runtime=state['runtime'] if state['runtime_python']==values['python'].get() else None
            plans=recommendations(state['models'],selected_cards(),runtime)
            if not plans:
                show('当前检测值下没有可推荐的本地 Agent 模型。\n可选择其他显卡、本地模型或手动参数后重新估算。\n若模型已经常驻，其占用不会重复算作可用显存；本工具不会自动停止它。')
                return
            p=plans[0]
            for k,v in {'model':p['model']['id'],'context':p['context'],
                        'kv':next(k for k,v in kv_options.items() if v==p['kv_cache_dtype']),
                        'prefill':p['prefill_tokens'],
                        'prefix':next(k for k,v in prefix_options.items() if v==p['prefix_mode']),
                        'speculation':'关闭'}.items():values[k].set(str(v))
            describe(p)
        except Exception as e:messagebox.showerror('推荐',str(e),parent=window)
    def preview():
        try:describe(plan())
        except Exception as e:messagebox.showerror('估算',str(e),parent=window)
    def use():
        try:
            # Re-measure immediately: a previous snapshot must not admit a stale plan.
            state['cards']=gpu_snapshot()
            p=plan();alias=p['model']['id']
            p['backend_alias']=alias
            speculation=speculation_options[values['speculation'].get()]
            entry=launch_entry(p,values['python'].get(),backend_url,alias,log,speculation)
            if (p['kv_cache_dtype']!='auto' or speculation!='off' or
                (p['model'].get('hybrid') and p['prefix_caching'] and not p.get('reference_qualification'))) and not messagebox.askokcancel(
                    '兼容性', '这些选项尚未在本机验证，可能无法启动或影响输出质量。保存供下次模型启动使用？',parent=window):return
            apply({'inference_recommendation':p,'model_launch':entry,'backend_model':alias,
                   'gpu_indices':p['gpu_indices']})
            window.destroy()
        except Exception as e:messagebox.showerror('配置',str(e),parent=window)
    buttons=ttk.Frame(frame);buttons.grid(row=10,column=0,columnspan=3,sticky='w')
    for label,action in [('检测',inspect),('采用推荐',recommend),('手动配置估算',preview),('保存选择',use)]:
        ttk.Button(buttons,text=label,command=action).pack(side='left',padx=5)
    def tick():
        if not window.winfo_exists():return
        while not events.empty():
            event,data=events.get_nowait();state['busy']=False
            if event=='error':show(data)
            else:
                state['cards'],found,state['runtime'],state['runtime_python']=data;state['models']=found['models']
                boxes['model']['values']=[m['id'] for m in state['models']]
                if state['models']:values['model'].set(state['models'][0]['id'])
                values['gpus'].set(','.join(str(c['index']) for c in state['cards']))
                recommend()
        window.after(100,tick)
    show('选择本地模型目录与 vLLM Python 程序，然后检测。\n推荐只做估算，不下载模型、不暂停挖矿、不发起推理。')
    window.after(100,tick)
