from __future__ import annotations

import argparse

from .execution import ActionRequest
from .io import load_request, save_take
from .operator import execute_configured_action
from .orchestrator import Puppeteer
from .performers import MockPerformer
from .stage import MockStage


def _execute(args):
    request=load_request(args.request)
    if args.stage!='mock': raise ValueError('Only mock stage is built in. Add a Stage adapter for real hardware.')
    performers=[MockPerformer()] if not args.no_performer else []
    take=Puppeteer(MockStage(),performers).execute(request,args.output_dir)
    save_take(args.out,take); print(args.out)

def _validate(args):
    req=load_request(args.request); print(f'valid {req.contract_version}: {req.shot_id}')

def _provider_action(args):
    from pathlib import Path

    request = ActionRequest.model_validate_json(Path(args.action).read_text(encoding="utf-8"))
    record = execute_configured_action(args.provider_config, request)
    print(record.model_dump_json())

def build_parser():
    p=argparse.ArgumentParser(prog='forge-puppeteer'); sub=p.add_subparsers(dest='cmd',required=True)
    v=sub.add_parser('validate'); v.add_argument('--request',required=True); v.set_defaults(func=_validate)
    e=sub.add_parser('execute'); e.add_argument('--request',required=True); e.add_argument('--out',required=True); e.add_argument('--output-dir',default='outputs'); e.add_argument('--stage',default='mock'); e.add_argument('--no-performer',action='store_true'); e.set_defaults(func=_execute)
    a=sub.add_parser('provider-action', help='explicitly execute one typed action using trusted operator provider config')
    a.add_argument('--provider-config', required=True); a.add_argument('--action', required=True, help='JSON ActionRequest including caller/session IDs')
    a.set_defaults(func=_provider_action)
    return p

def main(argv=None):
    args=build_parser().parse_args(argv); return args.func(args) or 0
if __name__=='__main__': raise SystemExit(main())
