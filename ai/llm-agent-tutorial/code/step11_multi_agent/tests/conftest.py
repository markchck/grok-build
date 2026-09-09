"""테스트 공통 설정.

모델 서버를 부르지 않는 테스트가 대부분이지만(``llm.chat``이 실패하면 모든
노드가 규칙 기반으로 대체되므로), CI 환경에 우연히 ``OPENAI_BASE_URL``이
실제 서버를 가리키게 설정돼 있어도 테스트가 네트워크를 타지 않도록 존재하지
않는 주소로 고정한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("CHAT_MODEL", "stub-model")
