"""The `agents` command.

agents init-db                                 create or upgrade the database
agents serve [--host H] [--port P]             start the API
agents worker [--agents cv,linkedin]           start a worker
agents create-key --project P --agent A        make a key (shown once)
agents list-keys [--project P]
agents revoke-key KEY_ID
agents try --agent A --key K --file F [...]    send files and print the result
agents try --agent A --key K --data '{...}'    send JSON (a lookup agent) and print the result
agents stats [--days N]                        usage and the job line
agents model-check [--agent A]                 does the model answer?
agents tool AGENT COMMAND [...]                an agent's own command (agents tool AGENT --help)
agents sweep                                   housekeeping now
agents generate-secret                         a random value for AGENTS_KEY_PEPPER
agents check                                   lint + all tests (developers)
"""

import argparse
import json
import logging
import secrets
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def build_parser():
    parser = argparse.ArgumentParser(prog='agents', description='Universal Agents service commands.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-db', help='create or upgrade the database')
    serve = sub.add_parser('serve', help='start the API')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--workers', type=int, default=1, help='API processes (Linux servers)')
    worker = sub.add_parser('worker', help='start a worker')
    worker.add_argument('--agents', default='', help='comma-separated; default: every switched-on agent')
    worker.add_argument('--once', action='store_true', help='run at most one job, then stop')
    key = sub.add_parser('create-key', help='make a key for one project and one agent')
    key.add_argument('--project', required=True)
    key.add_argument('--agent', required=True)
    key.add_argument('--label', default='')
    keys = sub.add_parser('list-keys')
    keys.add_argument('--project')
    revoke = sub.add_parser('revoke-key')
    revoke.add_argument('key_id')
    try_cmd = sub.add_parser('try', help='send files or JSON data to a running API and print the result')
    try_cmd.add_argument('--agent', required=True)
    try_cmd.add_argument('--key', required=True)
    given = try_cmd.add_mutually_exclusive_group(required=True)
    given.add_argument('--file', action='append', help='a file to upload (repeat for several)')
    given.add_argument('--data', help='JSON data for a lookup agent, for example \'{"number": "1"}\'')
    given.add_argument('--data-file', help='a .json file with the data for a lookup agent')
    try_cmd.add_argument('--url', default='http://127.0.0.1:8000')
    try_cmd.add_argument('--fields', default='')
    try_cmd.add_argument('--timeout', type=int, default=1800, help='seconds to wait for the result')
    stats = sub.add_parser('stats', help='usage per project and agent, and the job line')
    stats.add_argument('--days', type=int, default=7)
    model = sub.add_parser('model-check', help='send one tiny request to the model')
    model.add_argument('--agent', default='')
    accuracy = sub.add_parser('accuracy', help='run made-up documents through the real model and score them')
    accuracy.add_argument('--agent', default='')
    accuracy.add_argument('--cases', default='accuracy/cases.json')
    tool = sub.add_parser('tool', help="run an agent's own command, for example loading its reference data")
    tool.add_argument('agent')
    tool.add_argument('rest', nargs=argparse.REMAINDER)
    sub.add_parser('sweep', help='expire, return and delete now (workers do this every minute)')
    sub.add_parser('generate-secret', help='print a random value for AGENTS_KEY_PEPPER')
    sub.add_parser('check', help='lint and run all tests (needs the test extras)')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    # httpx logs full request addresses (which can hold an API key) at INFO.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    if args.command == 'generate-secret':
        print(secrets.token_urlsafe(48))
        return 0
    if args.command == 'check':
        return run_check()
    if args.command == 'try':
        return try_agent(args)
    from agents_service.services import build

    services = build()
    if args.command == 'init-db':
        services.db.upgrade()
        print('Database is up to date.')
    elif args.command == 'serve':
        import uvicorn

        services.db.upgrade()
        uvicorn.run(
            'agents_service.api.app:create_app',
            factory=True,
            host=args.host,
            port=args.port,
            workers=args.workers,
            access_log=False,
        )
    elif args.command == 'worker':
        return run_worker(services, args)
    elif args.command == 'create-key':
        return create_key(services, args)
    elif args.command == 'list-keys':
        from agents_service.core.keys import list_keys

        print(json.dumps(list_keys(services.db, args.project), indent=2))
    elif args.command == 'revoke-key':
        from agents_service.core.keys import revoke_key

        print('Revoked.' if revoke_key(services.db, args.key_id) else 'No such key.')
    elif args.command == 'stats':
        from agents_service.core.usage import usage_report

        print(
            json.dumps(
                {'line': services.jobs.waiting_counts(), 'usage': usage_report(services.db, args.days)},
                indent=2,
            )
        )
    elif args.command == 'tool':
        return run_tool(services, args.agent, args.rest)
    elif args.command == 'sweep':
        print(json.dumps(services.jobs.sweep()))
    elif args.command == 'model-check':
        return model_check(services, args.agent)
    elif args.command == 'accuracy':
        from agents_service.cli.accuracy import run as accuracy

        report = accuracy(services.settings, args.agent or None, args.cases)
        print(json.dumps(report, indent=2))
        return 0 if report['clean'] else 1
    return 0


def create_key(services, args):
    from agents_service.agents import registry
    from agents_service.core.keys import create_key as make

    try:
        agent = registry.load(args.agent)
        key_id, token = make(
            services.db, services.settings.key_pepper, args.project, agent.name, agent.key_prefix, args.label
        )
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    if agent.name not in services.agents:
        print(f'Note: the {agent.name} agent is not switched on (AGENTS_ENABLED_AGENTS).', file=sys.stderr)
    print(f'Key id: {key_id}')
    print(f'Key (shown only now, store it in the project backend settings): {token}')
    return 0


def run_tool(services, agent_name, argv):
    from agents_service.agents import registry
    from agents_service.agents.base import NotConfigured, ToolContext
    from agents_service.core.references import References

    try:
        agent = registry.load(agent_name)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    tools = agent.tools()
    parser = argparse.ArgumentParser(prog=f'agents tool {agent.name}')
    commands = parser.add_subparsers(dest='tool_command', required=True)
    for name, tool in tools.items():
        tool.add_arguments(commands.add_parser(name, help=tool.help))
    if not tools:
        print(f'The {agent.name} agent has no commands.', file=sys.stderr)
        return 2
    args = parser.parse_args(argv)
    try:
        agent_settings = agent.load_settings(services.settings)
    except NotConfigured as exc:
        agent_settings = None
        print(f'Note: not configured yet: {exc}', file=sys.stderr)
    context = ToolContext(
        settings=services.settings,
        agent_settings=agent_settings,
        references=References(services.db, services.storage, agent.name),
    )
    return tools[args.tool_command].run(context, args)


def run_worker(services, args):
    from agents_service.worker.runner import run

    stop = threading.Event()

    def ask_stop(*_):
        if stop.is_set():
            raise SystemExit(1)
        print('Stopping after the current job (press Ctrl+C again to stop now)...', file=sys.stderr)
        stop.set()

    signal.signal(signal.SIGINT, ask_stop)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, ask_stop)
    agents = [a.strip() for a in args.agents.split(',') if a.strip()] or None
    run(services, agents=agents, once=args.once, stop=stop)
    return 0


def model_check(services, agent_name):
    from agents_service.ai.providers import AIError, check_model, model_config

    names = [agent_name] if agent_name else [n for n, a in services.agents.items() if a.uses_model]
    status = 0
    for name in names:
        try:
            config = model_config(services.settings, name)
            started = time.monotonic()
            check_model(config)
            print(
                f'{name}: OK ({config["provider"]} / {config["model"]}, {time.monotonic() - started:.1f} s)'
            )
        except AIError as exc:
            print(f'{name}: FAILED - {exc.public_message}' + (f' ({exc.field})' if exc.field else ''))
            status = 1
    return status


def try_agent(args):
    """Send files or data like a project would, wait, and print the result JSON."""
    import httpx

    headers = {'Authorization': 'Bearer ' + args.key, 'Idempotency-Key': secrets.token_hex(12)}
    if args.file:
        files = []
        for path in args.file:
            file = Path(path)
            if not file.is_file():
                print(f'No such file: {path}', file=sys.stderr)
                return 2
            files.append(('files', (file.name, file.read_bytes(), 'application/octet-stream')))
        request = {'files': files, 'data': {'fields': args.fields} if args.fields else {}}
    else:
        try:
            data = json.loads(
                Path(args.data_file).read_text(encoding='utf-8-sig') if args.data_file else args.data
            )
        except (OSError, ValueError) as exc:
            print(f'The data is not valid JSON: {exc}', file=sys.stderr)
            return 2
        body = {'data': data, **({'fields': args.fields} if args.fields else {})}
        request = {'json': body}
    with httpx.Client(base_url=args.url, timeout=120, trust_env=False) as client:
        response = client.post(f'/v1/{args.agent}/jobs', headers=headers, **request)
        if response.status_code != 202:
            print(json.dumps(response.json(), indent=2))
            return 1
        job = response.json()
        print(f'Job {job["id"]} accepted; waiting for the result...', file=sys.stderr)
        deadline = time.monotonic() + args.timeout
        url = job['links']['self']
        while job['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(min(5, job.get('poll_after_seconds', 5)))
            response = client.get(url, headers={'Authorization': 'Bearer ' + args.key})
            if response.status_code == 429:
                time.sleep(int(response.headers.get('Retry-After', '5')))
                continue
            if response.status_code != 200:
                print(json.dumps(response.json(), indent=2))
                return 1
            job = response.json()
            if 'queue' in job:
                print(f'  waiting: position {job["queue"]["position"]}', file=sys.stderr)
    print(json.dumps(job, indent=2, ensure_ascii=False))
    return 0 if job.get('state') in ('done', 'partial') else 1


def run_check():
    root = Path(__file__).resolve().parents[2]
    if not (root / 'tests').is_dir():
        print('agents check runs from a source checkout (git clone + pip install -e ".[test]").')
        return 2
    steps = [
        [sys.executable, '-m', 'ruff', 'check', 'agents_service', 'tests'],
        [sys.executable, '-m', 'ruff', 'format', '--check', 'agents_service', 'tests'],
        [sys.executable, '-m', 'pytest', '-q'],
    ]
    for step in steps:
        print('$ ' + ' '.join(step[2:]), flush=True)
        if subprocess.run(step, cwd=root, check=False).returncode:
            print('Check FAILED.')
            return 1
    print('All checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
