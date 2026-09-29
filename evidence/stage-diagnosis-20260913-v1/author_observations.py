"""Materialize explicit, source-read AI annotations; never human or Judge labels.

Annotations below were authored after reviewing packet-v1's saved sources. This
is a development diagnostic, not an automatic semantic classification method.
The packet hash binds every case/index to its reviewed claim text and contexts.
"""
from pathlib import Path
import diagnose as d

PACKET = d.BASE / "packet-v1/packet.json"
PACKET_SHA = "75805c95ce92352ec43d35d31c5acd59916f4b4b5f36a27729d46f9479ed7c59"


def annotations():
    if d.sha(PACKET) != PACKET_SHA:
        raise ValueError("reviewed_packet_changed")
    packet = d.read(PACKET)
    by_id = {r["case_id"]: r for r in packet["items"]}
    rows = []

    def add(cid, index, status, relevance, reason, *quotes):
        cid = "shadow_" + cid
        claim = by_id[cid]["claims"][index]
        rows.append({"case_id": cid, "claim_index": index, "text": claim["text"],
                     "status": status, "relevance": relevance, "reason": reason,
                     "quotes": [{"source_number": q[0], "text": q[1],
                                 "field": q[2] if len(q) == 3 else "text"} for q in quotes]})

    def supported(cid, index, reason, *quotes, relevance="required"):
        add(cid, index, "source_supported", relevance, reason, *quotes)

    def partial(cid, index, reason, *quotes):
        add(cid, index, "partially_supported", "mixed", reason, *quotes)

    # The label concerns the rejected proposition, not whole-answer correctness.
    supported("emp_02", 0, "Source 1의 AI-POT 표 구간에 8/10~14 교육 및 필요시 8~9월 일정이 있다. ADsP 구간과 구분해 읽었다. gold 문서 ID와 다른 사본도 실제 근거일 수 있다.",
              (1, "AI프롬프트 활용 능력(AI-POT)<대면운영>"),
              (1, "2026. 8. 10.(월)"), (1, "2026. 8. 14.(금)"),
              (1, "2026. 8~9월 중(필요시)"))
    supported("emp_04", 0, "동일한 2026 제1차 KB굿잡 행사 본문의 일시와 일치한다. 일시 앞 회차는 횟수 수량이 아니라 행사 식별자다.",
              (6, "2026 제1차 KB굿잡 우수기업 취업박람회"), (6, "일시: 2026.04.27(월) 10:00~17:00"))
    supported("emp_04", 1, "동일 행사 Source 6 본문에 장소가 명시되어 있다. 제목만 있는 다른 사본이 아니라 실제 본문을 대조했다.",
              (6, "2026 제1차 KB굿잡 우수기업 취업박람회"), (6, "장소: 서울 COEX A홀"))
    supported("emp_05", 1, "재학생 집단 내 자격 제한은 원문과 일치한다. 모든 참가자가 재학생이어야 한다는 뜻으로 확장하지 않는다. 졸업예정자 등은 별도 대상이다.",
              (1, "■ 대상 : 만15~39세 청년 (졸업예정 및 졸업자, 재직자 가능)"),
              (1, "※재학생은 사이버대, 방송통신대, 야간대학 등의 재학생만 가능합니다."))
    partial("emp_06", 1, "날짜는 해냄/HNM 프로젝트에는 있으나 질문의 SKT 프로그램과 다른 사업 범위다. 문장에 주체가 생략되어도 질문의 주체를 상속하므로 올바른 필수정보 삭제 건으로 세지 않는다.",
            (3, "[해냄주식회사] '2026년 미래내일 일경험 지원사업 프로젝트형' 3-4월 프로젝트"),
            (3, "1차 프로젝트 2026년 03월 30일 (월) ~ 5월 24일 (일)"), (4, "운영기관: 해냄 주식회사 [(주)HNM]"))
    partial("emp_06", 2, "2차 날짜도 HNM 프로그램의 사실이다. 질문 SKT에 대한 답으로 복원하면 다른 프로그램 오염을 남긴다.",
            (3, "[해냄주식회사] '2026년 미래내일 일경험 지원사업 프로젝트형' 3-4월 프로젝트"),
            (3, "2차 프로젝트 2026년 04월 06일 (월) ~ 5월 31일 (일)"), (4, "운영기관: 해냄 주식회사 [(주)HNM]"))
    supported("adm_02", 0, "고지서 출력 시각은 표에 있으나 질문의 납부기간·은행·미납처리에는 부가 정보다. 복원된 필수정보로 세지 않는다.",
              (3, "등록금고지서 출력 2026. 7. 13.(월) 10:00"), relevance="optional")
    partial("adm_03", 2, "서류심사의 일반 설명은 맞지만 재정능력 요건을 묻는 질문에 금액·증명 기준을 제시하지 못한다. 요구된 재정 요건 전체가 초안에 있었다고 볼 수 없다.",
            (1, "하위과정 성적, 수학능력, 수학가능한 재정능력, 수학계획 및 자기소개서 등을 종합적으로 검토하여 합격/불합격으로 판정"))
    supported("adm_06", 0, "전기 모집 열의 원서접수 및 합격자 발표 행과 일치한다. 1/29의 연도는 같은 전기 열의 2027 면접 일정 및 2027학년도 범위로 해석했다. 단순 날짜 존재 판정이 아니다.",
              (5, "구 분\t특차 모집\t전기 모집\t후기 모집"),
              (5, "원서접수\t2026 9.14.(월)~9.18.(금)\t2026 12.7.(월)~12.11.(금)\t2027 5.10.(월)~5. 14.(금)"),
              (5, "면접고사\t10. 23.(금)\t2027 1. 7.(목)\t6. 11.(금)"),
              (5, "합격자 발표\t11. 13.(금)\t1. 29.(금)\t7. 2.(금)"))
    for i in (4, 5):
        supported("adm_08", i, "전기/후기 정원외 특별전형의 해당 열과 원서접수 및 서류 제출 행에 각각 일치한다. 표의 행·열을 함께 근거로 보존해야 한다.",
                  (1, "구 분\t전기 정원외 특별전형\t후기 정원외 특별전형"),
                  (1, "원서접수 및 서류 제출\t2026. 12. 7.( 월 ) ~ 12. 11.( 금 )\t2027. 5. 10.( 월 ) ~ 5. 14.( 금 )"))
    supported("intl_03", 0, "같은 국제협력실 연구조교 공지의 모집인원과 근무기간에 일치한다. 두 속성이 결합된 문장이므로 두 근거가 모두 필요하다.",
              (2, "2. 모집인원 : 1명"), (2, "4. 근무기간 : 2026. 9. 1. ~ 2027. 2. 28."))
    supported("intl_04", 0, "학생 온라인 지원 행의 시각·접수 시스템에 일치한다.",
              (1, "(학생)On-Line 지원\t2026. 7. 15.(수) 18:00까지\t스마트 학생지원시스템"))
    supported("intl_04", 1, "학생 서류 제출 행의 마감 및 제출처에 일치한다.",
              (1, "(학생)서류 제출\t2026. 7. 16.(목) 18:00까지\t소속 단과대학 행정실"))
    supported("intl_04", 2, "단과대학 취합 제출 행에 일치한다. 학생 직접 제출 기한과 혼동해서는 안 된다.",
              (1, "(단과대학)\n공문 및 학생서류(취합) 제출\t2026. 7. 17.(금) 18:00까지\t국제처로 제출"))
    supported("intl_04", 3, "결과 발표 행에 일치하며 초안도 예정이라는 한정을 보존한다.",
              (1, "결과 발표\t2026. 7. 23.(목) 18:00 (예정)\t국제처 홈페이지"))
    partial("intl_06", 4, "유효기간 예외와 5/8 날짜는 재외한국교육원장 추천 트랙 자료에 존재한다. 질문의 일반 외국인 전형 및 다른 gold 일정과 트랙 범위가 달라 무조건 정답 복원으로 세지 않는다. gold/코퍼스 범위의 사후 점검 대상이며 이번에 수정하지 않는다.",
            (3, "(국문) 2026학년도 후기 학부 외국인 특별전형 재외한국교육원장 추천 트랙 신입학 모집요강.pdf", "source_title"),
            (3, "최근 6개월 이내 발급분까지만 인정 (서류접수 마감일 2026. 5. 8.(금) 기준)"))
    supported("sch_02", 3, "해당 장학금의 신청대상에 나이와 미혼 요건이 함께 명시된다. 다른 자격 조건은 초안의 다른 문장에 있어 이 문장을 충분조건으로 해석하지 않는다.",
              (1, "[한국장학재단] 2026학년도 2학기 주거안정장학금 신청 안내"), (1, "- 만 39세 이하로 미혼인 자"))
    supported("sch_03", 0, "동일 국가근로장학금 1차 신청 공지의 시작·마감 일시와 일치한다.",
              (1, "2026학년도 2학기 국가근로장학금 1차 신청기간을 알려드리니"),
              (1, "가. 신청기간 : 2026. 5. 22.(금) 9시 ~ 2026. 6. 22.(월) 18시까지"))
    supported("sup_03", 3, "장애학생 컴퓨터교육 안내의 신청기간 마감일과 일치한다. 다른 속성의 날짜를 가져온 것이 아니다.",
              (1, "○ 신청기간 : ~ 2026. 7. 22.(수)까지"))
    supported("sup_04", 2, "운영 요일·시간·점심/공휴일 제외는 일치한다. 다만 전체 초안에는 원문의 방문 전 예약 필수가 빠져 있어 이 문장 복원만으로 전체 정답을 주장하지 않는다.",
              (7, "월요일~금요일 09:00~18:00 (점심시간, 공휴일 제외, *방문 전 예약 필수)"))
    supported("sup_04", 3, "성평등상담실 명칭과 전화번호가 같은 줄에서 연결된다.",
              (7, "[성평등상담실(성희롱·성폭력)] ☎ 051-510-7890"))
    supported("sup_04", 4, "인권상담실 명칭과 전화번호가 같은 줄에서 연결된다.",
              (7, "[인권상담실(인권침해 등)] ☎ 051-510-7942"))
    partial("grad_01", 0, "팀 인원·재학/휴학 자격은 있으나 원문 대학(원)을 초안부터 대학원으로 좁혀 학부생 범위를 누락했다. 회차 문제만 고쳐도 완전한 자격 답변이 되지 않는다.",
            (2, "• 참가 자격: 국내 대학(원) 재학(휴학)생 (개인 또는 3인 이하 팀)"))
    supported("core_03", 1, "서류 접수 행의 시작·마감과 일치한다. 연도는 같은 2026 프로그램 문서 범위다.",
              (2, "2026학년도 AI·SW 스텝업[Step-Up] 멘토링 대학생 멘토 모집"),
              (2, "서류 접수\n7. 9.(목) ~ 7. 30.(목) 23:59"))
    supported("core_03", 2, "활동기간에 두 축약 연도와 날짜가 직접 명시되어 있다.",
              (2, "3. 활동기간 : ‘26. 8. 18.(화) ~ ′27. 1. 31.(일)"))
    supported("core_04", 1, "GSAT 프로그램운영 행과 일치한다. NCS 행과 구분했다.",
              (2, "①\t대기업 (GSAT) 과정\t프로그램운영\t2026. 8. 5.(수) ~ 8. 7.(금)\t비대면"))
    for index, quote in [(2, "GSAT 추리 특강\t26. 8. 5.(수) 14:00~17:00"),
                         (3, "GSAT 수리 특강\t26. 8. 6.(목) 14:00~17:00"),
                         (4, "GSAT 모의고사\t26. 8. 7.(금) 10:00~22:00")]:
        supported("core_04", index, "질문의 운영일정에 직접 해당하는 GSAT 세부 강의/모의고사 행에 일치한다. gold 최소 요건보다 세부적이지만 질문 관련 필수 속성인 운영일정으로 분류했다.", (1, quote))
    for index in (0, 1, 2):
        supported("core_05", index, "같은 아카데미 표의 일시 및 장소 열에 일치한다. 다른 질문의 같은 시각·호실 번호를 빌리지 않았다.",
                  (2, "일 시\t인 원\t대 상\t장 소"),
                  (2, "8. 24.(월) ~ 8. 28.(금) 13:30 ~ 17:30\t50명 내외\t전국 대학(원)생 (선착순 접수)\t국회의정관 105호"))
    supported("core_06", 0, "제7회 동일 행사 제목과 접수기한 본문에 일치한다. 게시글 제목의 회차는 행사 정체성에 쓰되 본문의 날짜 근거와 구분해야 한다.",
              (1, "붙임1. (공고문 및 양식)제7회 부산 대학-지역 상생 아이디어 경진대회.pdf (282KB)", "source_title"),
              (1, "m 접수기한\n\n: 2026\n\n년 7 월 6 일 ( 월 ), 10:00 ~ 7 월 24 일 ( 금 ), 10:00 까지"))
    supported("core_06", 1, "Source 1이 이메일을 직접 지지한다. Source 2 개인정보 수집 양식은 같은 문서라도 이 이메일의 근거가 아니므로 추가 인용하면 안 된다.",
              (1, "메일주소 : ceb@btp.or.kr"))

    # Explicit manual list: no regex equates refusals with unsupported facts.
    for cid, index, relevance in [
        ("emp_08", 0, "required"), ("adm_02", 4, "irrelevant"),
        ("adm_03", 0, "required"), ("adm_07", 1, "required"),
        ("adm_07", 2, "required"), ("intl_02", 5, "irrelevant"),
        ("intl_06", 1, "required"), ("sch_01", 0, "required"),
        ("sch_04", 3, "required"), ("sup_01", 0, "required"),
        ("grad_02", 0, "required"), ("grad_04", 1, "required"),
        ("core_01", 3, "irrelevant")]:
        add(cid, index, "abstention_statement", relevance,
            "초안에 이미 확인 불가 또는 충돌 확인 필요라는 회피가 있다. 사실 문장을 후처리에서 잃은 건으로 세지 않는다. 이 분류만으로 회피의 적절성이나 검색 근거 부재를 판정하지 않는다.")

    notes = {"author_type": "assistant_ai", "human_review": False, "packet_sha256": PACKET_SHA,
             "label_definition": "required는 질문에서 요구한 속성과의 관련성이다. gold 전체 충족·GFC·독립 표본의 개선 상한을 뜻하지 않는다.",
             "rejected_claim_observations": sorted(rows, key=lambda r: (r["case_id"], r["claim_index"]))}
    d.validate_observations(packet, notes, PACKET_SHA)
    return notes


if __name__ == "__main__":
    d.publish(d.BASE / "notes-v1", {"ai-observations.json": annotations(),
              "input-sha256.json": {str(PACKET): PACKET_SHA, str(Path(__file__)): d.sha(__file__),
                                    str(Path(d.__file__)): d.sha(d.__file__)}})
