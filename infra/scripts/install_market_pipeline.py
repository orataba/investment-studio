#!/usr/bin/env python3
"""Write scheduled pipeline definitions; activation is always a separate action."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib


def definitions(scheduler, role, project_root, env_root, python_bin, log_root, label_prefix='com.orataba.investment-studio'):
    runner=project_root/'infra/scripts/run_market_pipeline.sh'
    actions=['sync'] if role=='replica' else ['daily','weekly','crypto','publish','sync','registered-prices-cn','registered-prices-hk','registered-prices-us','registered-prices-eu']
    result={}
    for action in actions:
        name='investment-studio-market-'+action
        if scheduler=='systemd':
            calendars={'daily':'*-*-* 07:15 Asia/Shanghai','weekly':'Sat *-*-* 11:00 Asia/Shanghai','crypto':'*-*-* 08:15 Asia/Shanghai','publish':'*-*-* *:40:00','sync':'*-*-* *:20:00',
                'registered-prices-cn':'Mon..Fri *-*-* 15:30 Asia/Shanghai','registered-prices-hk':'Mon..Fri *-*-* 17:00 Asia/Shanghai',
                'registered-prices-us':'Tue..Sat *-*-* 07:00 Asia/Shanghai','registered-prices-eu':'Tue..Sat *-*-* 07:05 Asia/Shanghai'}
            command=action
            if action.startswith('registered-prices-'):command='registered-prices --market '+action.rsplit('-',1)[1]
            def quote(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
            result[name+'.service']='\n'.join([
                '[Unit]',f'Description=Investment Studio market {action}',
                'After=network-online.target','Wants=network-online.target','',
                '[Service]','Type=oneshot',
                'Environment='+quote('ENV_ROOT='+str(env_root)),
                'Environment='+quote('PYTHON_BIN='+str(python_bin)),
                'Environment='+quote('INVESTMENT_STUDIO_MARKET_ROLE='+role),
                'ExecStart=/bin/bash '+quote(runner)+' '+command,
                'TimeoutStartSec=12h','StandardOutput=journal','StandardError=journal','']).encode()
            result[name+'.timer']='\n'.join([
                '[Unit]',f'Description=Investment Studio market {action} schedule','',
                '[Timer]','OnCalendar='+calendars[action],'Persistent=true',
                'Unit='+name+'.service','','[Install]','WantedBy=timers.target','']).encode()
        else:
            # Local installations synchronize hourly and once after login/wake.
            if role!='replica':raise ValueError('launchd is only used for the local replica')
            name=label_prefix+'.market-'+action
            result[name+'.plist']=plistlib.dumps({
                'Label':name,'ProgramArguments':['/bin/bash',str(runner),action],
                'EnvironmentVariables':{'ENV_ROOT':str(env_root),'PYTHON_BIN':str(python_bin),'INVESTMENT_STUDIO_MARKET_ROLE':role},
                'RunAtLoad':True,'StartInterval':3600,
                'StartCalendarInterval':{'Hour':8,'Minute':20},'ProcessType':'Background',
                'StandardOutPath':str(log_root/(name+'.log')),
                'StandardErrorPath':str(log_root/(name+'.error.log')),
            })
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scheduler',choices=['systemd','launchd'],required=True)
    parser.add_argument('--role',choices=['collector','replica'],required=True)
    parser.add_argument('--env-root',type=Path,required=True)
    parser.add_argument('--project-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--python',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--label-prefix',default=os.environ.get('LABEL_PREFIX','com.orataba.investment-studio'))
    args=parser.parse_args(argv)
    root=args.project_root.expanduser().resolve();env=args.env_root.expanduser().resolve()
    if env.is_relative_to(root):parser.error('The runtime environment directory must be outside the repository')
    output=args.output_dir or (Path.home()/'.config/systemd/user' if args.scheduler=='systemd' else Path.home()/'Library/LaunchAgents')
    logs=Path.home()/'.local/state/investment-studio/logs'
    data=definitions(args.scheduler,args.role,root,env,args.python or root/'.venv/bin/python',logs,args.label_prefix)
    output.mkdir(parents=True,exist_ok=True)
    if args.scheduler=='launchd':logs.mkdir(parents=True,exist_ok=True)
    for name,content in data.items():
        path=output/name;path.write_bytes(content);os.chmod(path,0o600)
        print(path)
    print('Definitions written. No timer or agent has been enabled or started.')
    return 0


if __name__=='__main__':raise SystemExit(main())
