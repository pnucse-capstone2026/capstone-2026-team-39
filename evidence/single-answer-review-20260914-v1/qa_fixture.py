"""Create a clearly synthetic browser QA packet; never uses production labels."""
import tempfile
from pathlib import Path
from common import canonical, publish, sha
from prepare import project

def main():
    root = Path(tempfile.mkdtemp(prefix='pnu-answer-review-qa-', dir='/private/tmp'))
    answer = {'answer': '합성 테스트: 신청 기한은 9월 30일입니다.', 'citations': [
                  {'source_number':1,'source_title':'합성 원문','claim_text':'합성 테스트: 신청 기한은 9월 30일입니다.',
                   'excerpt':'합성 신청 기한은 9월 30일입니다.','page':3,'section_path':['합성 일정'],'block_id':f'synthetic-{n}'}
                  for n in range(12)],
              'evaluation_trace': {'generation_input': {'user_prompt': '<UNTRUSTED_CONTEXT source_number="1" id="synthetic">\nText: 합성 신청 기한은 9월 30일입니다.\n</UNTRUSTED_CONTEXT>'}}}
    case = {'query': '[합성 QA 전용] 신청 기한은 언제인가요?', 'role': '합성 역할', 'answerable': True,
            'required_claims': [{'description': '합성 기한: 9월 30일', 'evidence_options': [{'source_title': '합성 원문', 'quote': '합성 신청 기한은 9월 30일입니다.'}]}]}
    packet = {'items': [project(answer, case, 'P01', 'practice'), project(answer, case, 'B001', 'main')], 'sample': {}}
    raw = canonical(packet)
    publish(root / 'packet.json', raw)
    publish(root / 'private-map.json', canonical({'packet_sha256': sha(raw), 'records': []}))
    print(root)

if __name__ == '__main__': main()
