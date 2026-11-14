"""Download verified official cloudflared binary if absent, then expose localhost over HTTPS."""
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path
import httpx

def main():
    executable=shutil.which('cloudflared')
    if not executable:
        target=Path('data/bin');target.mkdir(parents=True,exist_ok=True)
        system=platform.system();architecture=platform.machine()
        filename='cloudflared-darwin-arm64.tgz' if system=='Darwin' and architecture=='arm64' else 'cloudflared-linux-amd64' if system=='Linux' and architecture in ['x86_64','amd64'] else None
        if not filename: raise SystemExit('Install the official cloudflared client for this platform')
        with httpx.Client(follow_redirects=True,timeout=120) as client:
            release=client.get('https://api.github.com/repos/cloudflare/cloudflared/releases/latest');release.raise_for_status()
            asset=next(x for x in release.json()['assets'] if x['name']==filename)
            response=client.get(asset['browser_download_url']);response.raise_for_status()
        actual=hashlib.sha256(response.content).hexdigest()
        if asset.get('digest')!='sha256:'+actual: raise SystemExit('Missing or mismatched official SHA-256 digest')
        archive=target/filename;archive.write_bytes(response.content)
        if filename.endswith('.tgz'):
            with tarfile.open(archive) as tar:
                member=next(x for x in tar.getmembers() if x.name.lstrip('./')=='cloudflared' and x.isfile())
                binary=target/'cloudflared';binary.write_bytes(tar.extractfile(member).read())
        else: binary=archive
        binary.chmod(0o700);executable=str(binary.resolve())
        (target/'cloudflared-source.json').write_text(json.dumps({'release':release.json()['tag_name'],'sha256':actual,'url':asset['browser_download_url']},indent=2))
    subprocess.run([executable,'tunnel','--url','http://localhost:8000','--no-autoupdate'],check=True)

if __name__=='__main__':main()
