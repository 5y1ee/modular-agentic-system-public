"""제안서 인스턴스 설정."""
from modular_agentic.instances.profile import Profile

PROFILE = Profile(
    name="proposal",
    label="제안서",
    item_label="제안요청서",
    analyzer_role="제안요청서(RFP) 본문에서 제안사가 제안서로 답해야 하는 요구사항을 하나씩 뽑는 역할",
    analyzer_scope="제안사가 기능·구성·조건·기술지원으로 답할 수 있는 항목만 뽑는다. 사업 기간, 계약 조건, 제출 서류 형식, 평가 배점, 산출정보 목록은 제외한다.",
    analyzer_kind="kind에는 본문이 붙인 요구사항 번호와 명칭(예: 기능요구 6 AI Agent 권한)을 쓴다. 없으면 빈 문자열이다.",
    stages=("collect", "analyze"),
    collectors=("paste",),  # RFP는 사람이 받아 붙여 넣는다
)
