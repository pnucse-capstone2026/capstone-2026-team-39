"""Single-slot owned service worker. Key arrives only over a private stdin pipe."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading

import live_runner as live
bridge = live.bridge


def lease_watch(fd):
    # Parent death closes its exclusive pipe writer. Exit only this worker;
    # interrupted provider reservations remain consumed/uncertain in SQLite.
    while os.read(fd,1):
        pass
    os._exit(86)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--slot-id',required=True)
    parser.add_argument('--attestation-fd',type=int,required=True)
    parser.add_argument('--lease-fd',type=int,required=True)
    parser.add_argument('--nonce',required=True)
    parser.add_argument('--start-owned-server',action='store_true')
    args = parser.parse_args()
    bridge.require(args.start_owned_server,'explicit_owned_start_required')
    sys.addaudithook(live.audit)
    threading.Thread(target=lease_watch,args=(args.lease_fd,),daemon=True).start()
    bridge.file_sha(args.run_root/'policy.json')
    policy = bridge.strict_json((args.run_root/'policy.json').read_bytes())
    live.verify_runtime(policy)
    run = live.open_run(args.run_root,policy,create=False)
    slot = run.ledger.slot(args.slot_id)
    bridge.require(slot['role']=='generation','generation_worker_only')
    condition = policy['conditions'][slot['condition_id']]
    expected = live.clean_child_env(policy,condition)
    actual = {k:v for k,v in os.environ.items() if k.startswith(('RAG_','GEMINI_','GOOGLE_','PYTHON')) or k.lower().endswith('_proxy')}
    controls = {k:v for k,v in expected.items() if k.startswith(('RAG_','GEMINI_','GOOGLE_','PYTHON'))}
    bridge.require(actual==controls,'unclean_worker_environment')
    with run.ledger._transaction() as db:
        record = db.execute('SELECT nonce,binding,artifact_sha FROM executions WHERE slot=?',(args.slot_id,)).fetchone()
    bridge.require(record is not None and record['nonce']==args.nonce and record['binding'] is None
                   and record['artifact_sha'] is None,'worker_slot_not_fresh')
    raw = sys.stdin.buffer.readline(258)
    bridge.require(raw.endswith(b'\n') and len(raw)<=257,'invalid_key_pipe_frame')
    key = raw[:-1].decode('ascii')
    bridge.require(live.parse_key('RAG_GEMINI_API_KEY='+key)==key,'invalid_key_pipe_value')
    if policy['execution_mode']=='mock':
        bridge.require(key=='SYNTHETIC_OFFLINE_KEY','mock_must_not_receive_real_key')
    else:
        bridge.require(policy['api_execution_authorized'] and run.root==live.LIVE_ROOT,'live_worker_not_authorized')
    os.environ['RAG_GEMINI_API_KEY'] = key
    identity,bound = {},[]
    def identity_check():
        live.verify_runtime(policy)
        bridge.require(bool(bound),'worker_not_attested')
        fresh = bridge.guard.attest_bound_server(api,bound[0],condition,slot['index'],args.nonce,dict(sys.modules))
        bridge.require(fresh==identity,'worker_identity_changed')
        return fresh
    opener = live.MockGeneration(policy,slot) if policy['execution_mode']=='mock' else live.LiveProvider(run,args.slot_id)
    transport = live.PacedTransport(run.ledger,args.slot_id,opener=opener,identity_check=identity_check)
    sys.path.insert(0,str(Path(condition['root'])/'scripts'))
    with transport.installed():
        import search_api as api
        original = api.ThreadingHTTPServer
        def bind(address,handler):
            bridge.require(address==('127.0.0.1',0),'only_ephemeral_loopback_allowed')
            server = original(address,handler)
            try:
                identity.update(bridge.guard.attest_bound_server(api,server,condition,slot['index'],args.nonce,dict(sys.modules)))
                bound.append(server)
                data = bridge.canonical(identity)+b'\n'
                while data:
                    n = os.write(args.attestation_fd,data)
                    bridge.require(n>0,'attestation_pipe_closed')
                    data = data[n:]
                os.close(args.attestation_fd)
            except BaseException:
                server.server_close()
                raise
            return server
        api.ThreadingHTTPServer = bind
        missing = run.root/'intentionally-absent-dense-index'
        bridge.require(not missing.exists() and not missing.is_symlink(),'unexpected_dense_artifact')
        sys.argv = live.bootstrap.service_argv(api.__file__,slot['index']['path'],run.root)
        api.main()


if __name__ == '__main__':
    main()
