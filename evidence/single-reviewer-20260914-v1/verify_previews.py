"""Warm permitted source previews; emit only counts/hashes, never questions or labels."""
import json
import argparse
from pathlib import Path
import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-name', required=True)
    args = parser.parse_args()
    server.require(bool(server.re.fullmatch(r'preview-verification-v[0-9]+\.json', args.report_name)), 'invalid_report_name')
    cases, pin, _ = server.load_packet(False)
    sources = server.source_map(cases)
    documents = server.source_documents(cases)
    result = {'cases_sha256': pin, 'source_count': len(sources), 'external_llm_calls': 0, 'sources': []}
    for source_id, (path, _) in sources.items():
        row = {'source_id': source_id, 'extension': path.suffix.lower()}
        try:
            chunks = server.stored_chunks(sources, documents, source_id)
            row.update(chunk_count=len(chunks['chunks']), chunks_truncated=chunks['truncated'], source_sha256=chunks['source_sha256'])
        except Exception as exc:
            row['chunk_error_type'] = type(exc).__name__
        if path.suffix.lower() == '.hwp':
            try:
                converted = server.hwp_preview(sources, source_id, server.STATE / 'hwp-previews-v1')
                row.update(hwp='converted', html_sha256=converted['html_sha256'], stats=converted['stats'])
            except Exception as exc:
                row.update(hwp='failed', conversion_error_type=type(exc).__name__)
        result['sources'].append(row)
        print(json.dumps(row), flush=True)
    result['summary'] = {'chunk_sources_ok': sum('chunk_count' in row for row in result['sources']),
                         'hwp_ok': sum(row.get('hwp') == 'converted' for row in result['sources']),
                         'hwp_failed': sum(row.get('hwp') == 'failed' for row in result['sources'])}
    output = server.STATE / args.report_name
    server.publish(output, server.canonical(result))
    print(json.dumps({'summary': result['summary'], 'artifact': str(output), 'sha256': server.sha(output.read_bytes())}), flush=True)


if __name__ == '__main__':
    main()
