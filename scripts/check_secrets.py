"""Check staged (default) or tracked Git blobs without displaying secrets.

This lightweight check complements GitHub secret scanning. It is intentionally
dependency-free so it can also run as a local pre-commit hook.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'private key': rb'(?m)^-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----\r?$',
    'Hugging Face token': rb'\bhf_[A-Za-z0-9]{20,}\b',
    'GitHub token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b',
    'Runpod token': rb'\brpa_[A-Za-z0-9_-]{20,}\b',
    'Supabase secret': rb'\bsb_secret_[A-Za-z0-9_-]{20,}\b',
    'AWS access key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'live Stripe secret': rb'\bsk_live_[A-Za-z0-9]{20,}\b',
}

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

def private_path(name):
    p=Path(name)
    return ((bool(p.parts) and p.parts[0] in {'data','weights','checkpoints','third_party'})
        or any(part in {'node_modules','.venv'} for part in p.parts)
        or name.startswith(('docs/local/','docs/screenshots/'))
        or (p.name.startswith('.env') and p.name!='.env.example')
        or p.suffix.lower() in {'.pem','.key','.p12','.pfx','.db','.sqlite','.sqlite3'}
        or p.name in {'id_rsa','id_ed25519','deploy_key','app_token','runpod_app_token'})

def local_secrets():
    values=set()
    env=ROOT/'.env'
    if env.exists():
        for line in env.read_text().splitlines():
            key,sep,value=line.partition('=')
            if not sep or not re.search(r'TOKEN|PASSWORD|SECRET|API_KEY|ACCESS_KEY',key,re.I):continue
            value=value.strip()
            try:value=json.loads(value)
            except (ValueError,TypeError):value=value.strip('\'"')
            if isinstance(value,str) and len(value)>=12:values.add(value)
    for name in ['app_token','runpod_app_token']:
        p=ROOT/'data'/name
        if p.exists():
            value=p.read_text().strip()
            if len(value)>=12:values.add(value)
    return {variant.encode() for value in values for variant in (value,quote(value,safe=''))}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tracked',action='store_true',help='Check all indexed files rather than just staged changes')
    args=parser.parse_args()
    raw=git('ls-files','-z') if args.tracked else git('diff','--cached','--name-only','--diff-filter=ACMR','-z')
    files=[p.decode() for p in raw.split(b'\0') if p]
    known=local_secrets();failures=[]
    for name in files:
        if private_path(name):failures.append((name,'private file path'));continue
        data=git('show',':'+name)
        if len(data)>10*1024*1024:failures.append((name,'unexpected large file; review before publishing'))
        if any(value in data for value in known):failures.append((name,'matches a local credential'))
        for label,pattern in PATTERNS.items():
            if re.search(pattern,data):failures.append((name,label))
    if failures:
        for name,reason in failures:print(f'BLOCKED {name}: {reason}',file=sys.stderr)
        return 1
    print(f'Secret check passed: {len(files)} Git files; no excluded paths, known local credentials, or recognized secret patterns.')
    return 0

if __name__=='__main__':sys.exit(main())
