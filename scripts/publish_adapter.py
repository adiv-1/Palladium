"""Publish one private versioned adapter; never include recordings or labels."""
import argparse,json
from pathlib import Path
from apps.api.model_registry import publish_adapter

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter',required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--selection',choices=['selected','candidate','rejected'],default='candidate')
    parser.add_argument('--repo');parser.add_argument('--output')
    args=parser.parse_args()
    result=publish_adapter(args.adapter,args.version,args.selection,args.repo)
    if args.output:
        path=Path(args.output);temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2));temporary.replace(path)
    print(json.dumps(result),flush=True)

if __name__=='__main__':main()
