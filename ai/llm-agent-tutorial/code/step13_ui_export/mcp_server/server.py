"""docagent 문서·CSV MCP 서버 본체.

이 파일 하나가 "MCP 서버"다. 실행하면 이 프로세스가 표준입력/출력(stdio)
또는 HTTP로 MCP 프로토콜 요청을 받아 아래 세 가지 원시 개념(Primitive)을
제공한다.

- **Tools** (모델이 호출을 "결정"하고 애플리케이션이 실행하는 동작):
  ``search_docs``, ``sum_sales``, ``lookup_sales_rows``.
- **Resources** (모델의 판단 없이 호스트가 그냥 읽어서 컨텍스트에 붙이는
  정적/준정적 데이터): ``data://sales-schema``, ``docs://{doc_id}``.
- **Prompts** (서버가 미리 만들어 둔, 사람이나 클라이언트가 고르는 대화
  시작 템플릿): ``analyze_sales``.

세 가지를 전부 Tool로 만드는 것은 흔한 오해다(장 본문 2절 참고) — 이 서버는
일부러 셋을 구분해서 보여준다.

로컬 개발이므로 이 장의 기본 실행 방식은 **stdio** 전송이다(장 본문 3절
참고, transport 비교). ``mcp.run(transport="streamable-http", ...)``로
바꾸면 HTTP 기반 원격 배포로도 그대로 뜬다 — 그 경우를 확인한 결과는
장 본문 "검증 상태"에 남긴다.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_server.config import get_mcp_server_settings
from mcp_server.sales_data import (
    LookupSalesRowsArgs,
    SalesDataError,
    SumSalesArgs,
    csv_schema_description,
    lookup_sales_rows,
    sum_sales,
)
from mcp_server.search import EmbeddingError, dense_search

mcp = MCPServer(
    name="docagent-docs-sales",
    instructions=(
        "사내 문서 검색과 sales.csv 판매 데이터 조회 도구를 제공하는 MCP 서버다. "
        "매출 수치는 반드시 sum_sales/lookup_sales_rows로 확인하고, 매출 변화의 "
        "원인 설명은 search_docs로 찾은 문서 근거를 쓴다."
    ),
)


# ---------------------------------------------------------------------------
# Tools — 모델이 "이 도구를 이 인자로 부르고 싶다"고 판단하면 호스트가 실행한다.
# ---------------------------------------------------------------------------


@mcp.tool()
def search_docs(query: str, top_k: int = 5) -> dict:
    """사내 분기 리포트 문서에서 질의와 의미적으로 가까운 조각을 찾는다.

    매출 변화의 원인, 배경, 코멘트처럼 CSV 수치에 없는 서술형 정보를 찾을 때
    쓴다. Dense(의미) 검색만 지원한다 — 이 서버 프로세스에서 안정적으로
    동작하는 것을 확인한 방식이다(이유는 mcp_server/store.py 상단 주석과
    장 본문 4-2절 참고).
    """
    if not query.strip():
        raise ToolError("query가 비어 있다. 검색어를 입력해달라.")
    top_k = max(1, min(top_k, 20))
    try:
        hits = dense_search(query, limit=top_k)
    except EmbeddingError as exc:
        raise ToolError(f"문서 검색을 위한 임베딩 호출에 실패했다: {exc}") from exc
    except Exception as exc:  # Milvus 연결 실패 등 — 원인을 그대로 알려준다
        raise ToolError(f"문서 검색 중 오류가 발생했다: {exc}") from exc

    if not hits:
        return {"matched_count": 0, "hits": []}
    return {"matched_count": len(hits), "hits": hits}


@mcp.tool(name="sum_sales")
def sum_sales_tool(product: str | None = None, region: str | None = None, quarter: str | None = None) -> dict:
    """sales.csv에서 조건에 맞는 매출·판매수량 합계를 계산한다.

    제품별/지역별/분기별 매출 합계를 물을 때 쓴다. 개별 거래 내역이 아니라
    합계 숫자가 필요할 때 이 도구를 쓴다(개별 행이 필요하면
    lookup_sales_rows를 쓴다).
    """
    try:
        args = SumSalesArgs(product=product, region=region, quarter=quarter)
    except ValidationError as exc:
        raise ToolError(f"입력 인자가 올바르지 않다: {exc}") from exc
    try:
        return sum_sales(args)
    except SalesDataError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(name="lookup_sales_rows")
def lookup_sales_rows_tool(
    product: str | None = None,
    region: str | None = None,
    quarter: str | None = None,
    limit: int = 10,
) -> dict:
    """sales.csv에서 조건에 맞는 판매 기록 원본 행을 최대 limit개까지 반환한다.

    합계가 아니라 날짜별 개별 거래 내역을 확인할 때 쓴다.
    """
    try:
        args = LookupSalesRowsArgs(product=product, region=region, quarter=quarter, limit=limit)
    except ValidationError as exc:
        raise ToolError(f"입력 인자가 올바르지 않다: {exc}") from exc
    try:
        return lookup_sales_rows(args)
    except SalesDataError as exc:
        raise ToolError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Resources — 모델이 "호출을 결정"하지 않는다. 호스트/클라이언트가 URI로
# 직접 읽어서 컨텍스트에 붙이거나 사용자에게 보여준다.
# ---------------------------------------------------------------------------


@mcp.resource("data://sales-schema", mime_type="text/plain")
def sales_schema_resource() -> str:
    """정적 리소스: sales.csv의 컬럼 구성 설명.

    Tool로 만들 수도 있었지만(예: get_csv_schema() 도구) 일부러 Resource로
    뒀다 — 인자도, 부수효과도 없고, 매번 모델이 "호출할지 판단"할 필요 없이
    호스트가 대화 시작 시 그냥 컨텍스트에 붙여도 되는 정보이기 때문이다.
    """
    return csv_schema_description()


@mcp.resource("docs://{doc_id}", mime_type="text/markdown")
def doc_resource(doc_id: str) -> str:
    """리소스 템플릿: 문서 원문 전체를 doc_id로 직접 읽는다.

    search_docs Tool과의 차이: 이건 "검색"이 아니라 "이미 doc_id를 아는
    상태에서 원문 전체를 그대로 가져오기"다. 근거 링크(5단계 규약)를 눌러
    원문을 열어보는 상황과 같은 모양이라, 모델의 판단이 필요한 동작(Tool)이
    아니라 정해진 대상을 그대로 가져오는 동작(Resource)으로 모델링했다.
    """
    docs_dir = get_mcp_server_settings().sales_csv_path.parent / "docs"
    path = docs_dir / f"{doc_id}.md"
    if not path.exists():
        from mcp.server.mcpserver.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError(f"문서 '{doc_id}'를 찾을 수 없다.")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompts — 서버가 미리 만들어 둔 대화 시작 템플릿. 모델이 스스로 고르지
# 않는다. 클라이언트(또는 그 뒤의 사용자)가 "이 프롬프트를 쓰겠다"고
# 선택해서 가져온다.
# ---------------------------------------------------------------------------


@mcp.prompt()
def analyze_sales(product: str, quarter: str) -> str:
    """제품·분기를 지정해 매출 변화 원인 분석을 요청하는 프롬프트 템플릿을 만든다."""
    return (
        f"{product}의 {quarter} 매출을 sum_sales 도구로 확인하고, "
        f"직전 분기와 비교해 증감을 계산해라. 증감의 원인을 search_docs로 "
        f"관련 리포트에서 찾아 근거와 함께 설명해라."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
