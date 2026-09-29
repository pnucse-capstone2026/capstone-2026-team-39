"""Read-only live API verification; no questions, chunks, HTML or human labels printed."""
import json
import urllib.request
import server


def main():
    token = (server.STATE / 'token').read_text().strip()
    def request(path, body=None):
        headers = {'X-Review-Token': token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request('http://127.0.0.1:8772' + path, body, headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read())
    before = request('/api/state')
    health = request('/health')
    packet = request('/api/bootstrap')
    sources = server.source_map(packet['cases'])
    results = []
    for source_id, (path, _) in sources.items():
        chunks = request('/api/source-chunks/' + source_id)
        row = {'source_id': source_id, 'chunk_count': len(chunks['chunks']), 'truncated': chunks['truncated']}
        if path.suffix.lower() == '.hwp':
            preview = request('/api/source-hwp-preview/' + source_id, b'{}')
            assert server.sha(preview['html'].encode()) == preview['html_sha256']
            row.update(hwp_html_sha256=preview['html_sha256'], stats=preview['stats'])
        results.append(row)
    after = request('/api/state')
    result = {'health': health, 'sources': results,
              'before': {k: before[k] for k in ('revision', 'sha256', 'locked')},
              'after': {k: after[k] for k in ('revision', 'sha256', 'locked')},
              'review_unchanged_during_check': before['revision'] == after['revision'] and before['sha256'] == after['sha256'],
              'source_count': len(results), 'chunk_count': sum(r['chunk_count'] for r in results),
              'hwp_count': sum('hwp_html_sha256' in r for r in results), 'review_write_requests': 0}
    output = server.STATE / 'live-preview-verification-v1.json'
    server.publish(output, server.canonical(result))
    print(json.dumps({k: v for k, v in result.items() if k != 'sources'}))
    print(json.dumps({'artifact': str(output), 'sha256': server.sha(output.read_bytes())}))


if __name__ == '__main__':
    main()
