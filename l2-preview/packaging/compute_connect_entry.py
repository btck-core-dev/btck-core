"""Frozen desktop entry; keeps worker imports explicit for the build."""
import sys

if __name__ == '__main__':
    if '--mining-lease-guard' in sys.argv:
        sys.argv.remove('--mining-lease-guard')
        from worker.mining_guard import main
        main()
    elif '--node-config' in sys.argv:
        import os
        from pathlib import Path
        index=sys.argv.index('--node-config')
        config=Path(sys.argv[index+1]).resolve()
        check='--check' in sys.argv
        sys.argv=[sys.argv[0],'--config',str(config)]+(['--check'] if check else [])
        log=config.with_name('compute-node.log')
        with os.fdopen(os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600),'w',buffering=1) as output:
            sys.stdout=sys.stderr=output
            from worker.compute_node import main
            main()
    elif '--smoke-check' in sys.argv or '--advisor-smoke-check' in sys.argv:
        # Make a headless Windows build failure observable without a modal dialog.
        import os
        from pathlib import Path
        import traceback
        log = Path(os.environ.get('TEMP', '.')) / 'btck-compute-connect-smoke.log'
        with log.open('w', encoding='utf-8') as output:
            try:
                output.write('entry\n'); output.flush()
                from worker.connect_app import main
                output.write('imported\n'); output.flush()
                main()
                output.write('PASS: native UI event loop exited\n')
            except Exception:
                traceback.print_exc(file=output)
                raise SystemExit(2)
    else:
        from worker.connect_app import main
        main()
