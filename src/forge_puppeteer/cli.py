from __future__ import annotations
import argparse
from .io import load_request, save_take
from .orchestrator import Puppeteer
from .stage import MockStage
from .performers import MockPerformer

def _execute(args):
    request=load_request(args.request)
    if args.stage!='mock': raise ValueError('Only mock stage is built in. Add a Stage adapter for real hardware.')
    performers=[MockPerformer()] if not args.no_performer else []
    take=Puppeteer(MockStage(),performers).execute(request,args.output_dir)
    save_take(args.out,take); print(args.out)

def _validate(args):
    req=load_request(args.request); print(f'valid {req.contract_version}: {req.shot_id}')

def build_parser():
    p=argparse.ArgumentParser(prog='forge-puppeteer'); sub=p.add_subparsers(dest='cmd',required=True)
    v=sub.add_parser('validate'); v.add_argument('--request',required=True); v.set_defaults(func=_validate)
    e=sub.add_parser('execute'); e.add_argument('--request',required=True); e.add_argument('--out',required=True); e.add_argument('--output-dir',default='outputs'); e.add_argument('--stage',default='mock'); e.add_argument('--no-performer',action='store_true'); e.set_defaults(func=_execute)
    return p

def main(argv=None):
    args=build_parser().parse_args(argv); return args.func(args) or 0
if __name__=='__main__': raise SystemExit(main())
