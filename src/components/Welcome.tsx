import { ArrowUpRight, BookOpen } from 'lucide-react'
import { sanjiniSrc } from '../chat/constants'
import type { SuggestedQuestion } from '../chat/constants'

export function WelcomeHero() {
  return (
    <section className="welcome">
      <div className="welcome-symbol">
        <img alt="" src={sanjiniSrc} />
        <span>
          <BookOpen size={13} />
          문서에 근거한 답변
        </span>
      </div>
      <h2>
        복잡한 공문서,
        <br />
        <span>쉽게 물어보세요.</span>
      </h2>
      <p>
        학교 규정부터 기관 자료까지,
        <br className="mobile-only" /> 필요한 내용을 출처와 함께 찾아드려요.
      </p>
    </section>
  )
}

export function SuggestedQuestions({
  questions,
  onSelect,
}: {
  questions: SuggestedQuestion[]
  onSelect: (question: SuggestedQuestion) => void
}) {
  return (
    <section aria-label="추천 질문" className="suggestion-area">
      <div className="suggestion-heading">
        <span>이렇게 질문해 보세요</span>
        <span>질문을 눌러 시작하기</span>
      </div>
      <div className="suggestion-grid">
        {questions.map((item) => (
          <button
            className="suggestion-card"
            key={item.question}
            onClick={() => onSelect(item)}
            type="button"
          >
            <span className="suggestion-category">
              <item.icon size={17} />
              {item.category}
            </span>
            <strong>{item.label}</strong>
            <span className="suggestion-footer">
              {item.institution}
              <ArrowUpRight size={15} />
            </span>
          </button>
        ))}
      </div>
    </section>
  )
}
