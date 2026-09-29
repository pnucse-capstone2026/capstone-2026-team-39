"""Produce immutable local verification evidence, never human labels."""
import subprocess
import argparse
import re
from common import BASE, HERE, ROOT, canonical, publish, read, require, sha, utc

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-name',default='validation-v1');args=parser.parse_args()
    require(bool(re.fullmatch(r'validation-v[0-9]+',args.output_name)),'invalid_validation_name')
    commands=[['python3','-B','-m','unittest','discover','-s',str(HERE.relative_to(ROOT)),'-p','test_*.py'],
              ['node','--check',str((HERE/'app.js').relative_to(ROOT))],['git','diff','--check']]
    results=[]
    for command in commands:
        result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
        results.append({'command':command,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    record={'created_at':utc(),'provider_calls':0,'commands':results,
            'source_sha256':{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [*sorted(HERE.glob('*.*')),ROOT/'docs/single-answer-agreement-handoff-20260914.md'] if p.is_file()},
            'packet_sha256':sha((BASE/'packet.json').read_bytes()),'private_map_sha256':sha((BASE/'private-map.json').read_bytes()),
            'browser_qa':{'fixture_path':'/private/tmp/pnu-answer-review-qa-zl_q3qt3',
                          'observed':'Synthetic QA only: notes+score+GFC survived reload at r1; first confirmation r2 locked fields; download verified and server export persisted.',
                          'export_sha256':'ffe750d37e729b8ff71171ec9efe6bb1ed6ccecb877464cfd022827ee5846eba'},
            'actual_human_labels_created_by_agent':0}
    out=BASE/args.output_name;out.mkdir(mode=0o700)
    publish(out/'verification.json',canonical(record))
    print({'path':str(out/'verification.json'),'sha256':sha((out/'verification.json').read_bytes()),'all_commands_passed':all(r['exit_code']==0 for r in results)})
    if any(r['exit_code'] for r in results):raise SystemExit(1)

if __name__=='__main__':main()
