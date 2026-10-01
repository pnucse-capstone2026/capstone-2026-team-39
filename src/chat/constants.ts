import {
  Award,
  BookOpen,
  FileText,
  Globe,
  GraduationCap,
  Landmark,
  ListChecks,
  ShieldCheck,
} from 'lucide-react'
import type { RoleOption } from '../api/rag'
import { positiveIntegerSetting } from './settings'

export const MAX_QUESTION_CHARS = positiveIntegerSetting(
  import.meta.env.VITE_MAX_QUESTION_CHARS,
  1000,
)
export const MAX_ROLE_CHARS = 120
export const noRole = 'none'
export const customRoleId = 'custom'
// 서버 /health가 프리셋을 내려주지 못할 때의 예비 목록 (role_router와 동일).
export const fallbackRoleOptions: RoleOption[] = [
  { id: 'pnu-student', label: '부산대학교 학생' },
  { id: 'pnu-staff', label: '부산대학교 행정직원' },
  { id: 'pnu-researcher', label: '부산대학교 연구자' },
  { id: 'fss-staff', label: '금융감독원 직원' },
  { id: 'bok-staff', label: '한국은행 직원' },
  { id: 'krx-staff', label: '한국거래소 직원' },
  { id: 'ksd-staff', label: '한국예탁결제원 직원' },
  { id: 'kisa-staff', label: '한국인터넷진흥원 직원' },
  { id: 'kiost-staff', label: '한국해양과학기술원 직원' },
]
export const MAX_SOURCE_LOCATIONS = 3
export const MAX_DETAIL_LOCATIONS = 8
export const sanjiniSrc = '/sanjini.webp'
export const allInstitutions = '전체 기관'
export const evidenceScopePresets = [
  {
    value: 4,
    label: '정밀',
    detail: '관련성이 높은 근거에 집중합니다.',
  },
  {
    value: 8,
    label: '균형',
    detail: '정확도와 검색 범위의 균형을 맞춥니다.',
  },
  {
    value: 12,
    label: '확장',
    detail: '여러 규정과 문서를 폭넓게 살핍니다.',
  },
] as const
export type EvidenceTopK = (typeof evidenceScopePresets)[number]['value']
export const defaultEvidenceTopK: EvidenceTopK = 8
export const defaultInstitutions = [
  allInstitutions,
  '금융감독원',
  '한국은행',
  '한국거래소',
  '한국예탁결제원',
  '부산대학교',
  '한국인터넷진흥원(KISA)',
  '한국해양과학기술원',
]

// 2026-10-01 시연 전 실제 호출로 근거 연결을 확인한 질문 (logs/demo-questions-20261001.json).
export const suggestedQuestions = [
  {
    label: '기숙사 공지는 어디서 보나요?',
    institution: '부산대학교',
    category: '학교 생활',
    question: '대학생활원 공지사항은 어디서 확인하고 개별 문자도 보내주나요?',
    icon: GraduationCap,
  },
  {
    label: '교환학생 지원 자격이 궁금해요',
    institution: '부산대학교',
    category: '국제 교류',
    question: '교환학생 프로그램 지원 자격을 알려줘',
    icon: Globe,
  },
  {
    label: '신탁 현황을 요약해 주세요',
    institution: '금융감독원',
    category: '핵심 요약',
    question: '신탁 수탁고 현황을 찾아서 요약해줘',
    icon: FileText,
  },
  {
    label: '지급결제 리스크를 알려줘요',
    institution: '한국은행',
    category: '문서 탐색',
    question: '한국은행 지급결제 리스크 관련 내용을 설명해줘',
    icon: BookOpen,
  },
  {
    label: '코스닥 상장심사 절차는?',
    institution: '한국거래소',
    category: '제도 이해',
    question: '코스닥 상장심사 절차를 알려줘',
    icon: Landmark,
  },
  {
    label: 'e-SAFE 사용자 신고 방법',
    institution: '한국예탁결제원',
    category: '절차 안내',
    question: 'e-SAFE 사용자 신고는 어떻게 하나요?',
    icon: ListChecks,
  },
  {
    label: '임직원 행동강령 핵심 내용',
    institution: '한국인터넷진흥원(KISA)',
    category: '규정 확인',
    question: 'KISA 임직원 행동강령의 주요 내용을 알려줘',
    icon: ShieldCheck,
  },
  {
    label: '승진심사 기준을 알려줘요',
    institution: '한국해양과학기술원',
    category: '규정 확인',
    question: '승진심사 기준을 알려줘',
    icon: Award,
  },
]
export type SuggestedQuestion = (typeof suggestedQuestions)[number]
