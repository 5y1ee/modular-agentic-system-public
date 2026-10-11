# AgentCore 조사 — 우리 시스템에 어디에 어떻게 쓸 수 있나 (2026-10-05)

## 이 문서를 읽는 법
- **출처**: AWS 공식 문서(`docs.aws.amazon.com`)와 가격 페이지를 도구로 직접 열어 읽었다. 도구가 페이지를 요약해서 돌려주므로, **중요한 숫자는 아래 출처 링크의 원문으로 한 번 더 확인**하는 것이 안전하다.
- 표기: **[확인]** 공식 문서에 적힌 것, **[추정]** 문서에서 추론한 것(내 판단), **[미확인]** 문서에서 못 찾은 것.
- 이 조사는 "DevCraft에서 AgentCore 기본 기능을 써 보고 싶다"는 사용자 의견에 따른 것이다. 어디까지 요구되는지는 확인하지 않았다.

## 1. 한 줄 요약
AgentCore는 **에이전트를 운영하는 관리형 부품 모음**이다. 우리 시스템에서는 ① **Runtime**(파이프라인을 올려 실행·호출), ② **Gateway**(vault 검색을 MCP 도구로 노출), ③ **Observability**(실행 기록·지표)가 가장 자연스럽게 맞고, **Memory**는 쓸 수 있지만 선택이다. 가장 큰 설계 쟁점은 **"클라우드의 Runtime·Gateway가 사용자 서버의 vault와 qmd에 어떻게 닿는가"**다.

## 2. 구성 요소별: 무엇이고 우리 시스템 어디에 맞나

| 구성 요소 | 무엇 | 우리 시스템에서 | 판단 |
|---|---|---|---|
| **Runtime** | 에이전트 코드를 서버리스로 실행·확장. 세션마다 격리된 microVM. 프레임워크 무관(Strands 포함) | 백엔드 포팅(`pipeline` 호출부)을 여기에 올릴 수 있다 | 맞음 |
| **Gateway** | API·Lambda·MCP 서버를 하나의 MCP 엔드포인트로 노출. 인바운드·아웃바운드 인증 관리 | vault 검색(`search_vault`)을 도구로 노출 → Strands·Quick이 같은 도구를 쓴다 | 맞음 |
| **Memory** | 단기(세션 이벤트)·장기(전략으로 추출한 사실·선호·요약) 메모리 | 피드백·작성 지침, 회사별 지원 이력 | 선택(아래) |
| **Identity** | 에이전트·도구의 인증·자격증명 관리 | 프런트→백엔드 인증, Gateway의 외부 도구 인증 | 나중에 |
| **Observability** | 지연·토큰·도구 호출·오류 지표, OpenTelemetry·CloudWatch 연동 | 실행 기록·비용·시간 추적(신뢰성 숙제) | 맞음 |
| Browser·Code Interpreter | 격리된 브라우저·코드 실행 | 지금은 필요 없음 | 해당 없음 |
| Evaluations·AB Testing·Policy·Registry 등 | 문서의 한도 표에 이름이 있다 | **읽지 않았다** | [미확인] |

### 2.1 Runtime
- [확인] 두 가지 방식으로 올린다: **직접 코드 배포(zip, 압축 250MB·풀면 750MB 이하)**와 **컨테이너 이미지(2GB 이하)**. 세션 생성 속도는 직접 배포 25/초, 컨테이너 1.6/초로 문서에 적혀 있다.
- [확인] 호출 규약(HTTP): 컨테이너/앱이 **포트 8080**에서 `/invocations`(요청)와 `/ping`(상태)을 구현한다. 인증은 SigV4 또는 OAuth 2.0. `bedrock-agentcore` SDK의 `BedrockAgentCoreApp`과 `@app.entrypoint`가 이 규약을 대신 처리한다.
- [확인] **한 번의 동기 요청은 최대 15분**, **스트리밍은 최대 60분**, **비동기 작업은 최대 8시간**, 요청·응답 최대 100MB. 세션당 하드웨어는 **최대 2 vCPU / 8GB**(GPU 언급 없음). 유휴 세션은 기본 15분 뒤 종료.
- [확인] **비동기 모델**: 에이전트가 작업을 시작하고 바로 "시작했다"고 응답한 뒤 백그라운드에서 계속 처리하고, 사용자가 나중에 같은 세션으로 다시 확인할 수 있다. 상태는 `/ping`이 `"HealthyBusy"`를 돌려주는 것으로 알린다(`add_async_task`/`complete_async_task`). 주의: `@app.entrypoint` 핸들러가 블로킹되면 `/ping`도 막혀 15분 뒤 세션이 끊길 수 있다.
- [확인] Runtime을 **VPC에 연결**할 수 있다(ENI를 사용자의 VPC에 만들어 RDS 같은 사설 자원에 접근). **us-west-2**가 지원 지역 표에 있다(서버 프로필 `user`의 리전과 같다).
- [추정] 우리 한 건 처리는 약 4분이라 **동기 호출 한도(15분) 안**이다. 그래도 프런트 연결을 오래 붙잡는 것은 별개 문제라 백엔드 쪽에 작업 큐 구조는 여전히 필요하다.
- [미확인] 직접 코드 배포가 우리 의존성(`strands-agents`, `boto3`, `pydantic` 등)을 250MB 안에 담는지, ARM64 등 플랫폼 요구는 측정하지 않았다.

### 2.2 Gateway
- [확인] 대상(target) 종류: **MCP 대상**(Lambda, API Gateway REST, OpenAPI, Smithy, **MCP 서버**, 통합 제공 템플릿·커넥터), **HTTP 대상**, **추론 대상**. 도구 목록을 하나의 가상 MCP 서버로 합쳐 주고 의미 검색으로 도구를 고르게 할 수도 있다.
- [확인] **MCP 서버 대상**: 외부 MCP 서버를 URL로 등록하면 `tools/list` 등을 동기화해 목록에 올린다. 지원 MCP 프로토콜 버전은 문서에 `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`. 아웃바운드 인증은 **인증 없음(권장 안 함)**, **OAuth**, **IAM(SigV4)**, **API 키**. SigV4는 AWS 서비스 뒤의 서버에만 쓸 수 있고, **ALB나 EC2 엔드포인트 직접은 안 된다**(그럴 땐 OAuth나 API 키).
- [미확인] **Gateway가 인터넷에 공개되지 않은 사설 서버를 대상으로 삼을 수 있는지**는 읽은 문서에 적혀 있지 않다(MCP 서버 대상 페이지에 "공개 접근 필요"도 "사설 연결 가능"도 명시가 없었다). VPC 연결 문서는 Runtime과 내장 도구(Browser, Code Interpreter)만 다룬다.
- [확인, 2026-10-05 직접 시험] qmd(2.8.3)의 MCP 서버는 `POST /mcp`(스트림 이벤트 형식 응답: `event: message` / `data: {…}`)로 **도구 4개**를 노출한다: `query`(인자 `searches`, `limit`, `minScore`, `candidateLimit`, `collections`, `intent`, `rerank`), `get`, `multi_get`, `status`. `query`를 `vec` 하위 질의 + `rerank:false`로 불러 `FINT.md`를 1위로 돌려받았다. **`rerank`의 기본값이 true**라 이 서버에서는 호출마다 `rerank:false`를 줘야 한다(리랭킹은 쿼리당 9~21분이라 쓰지 않기로 한 것 — 레포 README). 모델이 인자를 알아서 채우는 구조라 이 값을 도구 정의만으로 막을 수 없다.

### 2.3 Memory
- [확인] 단기 메모리는 세션의 턴별 이벤트를 저장한다. 장기 메모리는 이벤트에서 **전략(semantic, summarization, user preference, episodic, custom)**으로 사실·선호·요약을 비동기로 추출해 세션을 넘어 의미 검색으로 꺼낸다. 이벤트 만료는 7~365일, 메모리 리소스당 전략은 최대 6개.
- [추정] 우리 피드백 루프의 "일반 지침"은 장기 메모리의 **user preference** 전략과 모양이 비슷하다. 다만 우리는 **사용자가 승인한 지침만** 적용하는 설계라, 자동 추출만으로 쓰면 그 원칙과 부딪친다. 쓴다면 "추출된 후보를 `GuidanceProposer` 후보로 받고 승인은 그대로 둔다"가 맞다. 회사별 지원 이력도 같은 방식으로 맡길 수 있다.
- [판단] vault 자체가 이미 장기 기억 역할을 하고, 지침은 파일·DB로 충분히 저장된다. Memory는 **AgentCore 기능을 써 보는 것 자체가 목적일 때** 가치가 크다.

## 3. 비용 (공식 가격 페이지의 숫자, 미국 달러)
- [확인] Runtime 소비형(v2): **$0.1276/vCPU-시간, $0.0169/GB-시간**(v1: $0.0895, $0.00945). 약정형 기준선은 "2026년 10월 출시"로 적혀 있다. v1과 v2 중 우리 계정에 어느 쪽이 적용되는지는 [미확인].
- [확인] Gateway: API 호출 **$0.005/1,000회**, 검색 API $0.025/1,000회, 도구 색인 $0.02/100도구·월.
- [확인] Memory: 장기 저장 $0.75/1,000레코드·월, 장기 검색 $0.50/1,000회. 단기는 수집 $1.00/GB, 검색 $0.20/GB, 저장 $0.10/GB·월(**"2026-10-06 기준"으로 표기**).
- [확인] Identity: Runtime·Gateway를 거치면 무료. Observability: 별도 요율 없이 **CloudWatch 요금**. 신규 고객에게 $200 크레딧이 적용될 수 있다고 적혀 있다.
- [추정, 청구 방식은 미확인] 작업 한 건(약 4분)에 2 vCPU·8GB를 모두 쓴다고 가정하면 Runtime 비용은 대략 **한 건에 $0.03 안팎**이다. 모델(Bedrock) 토큰 비용이 이보다 훨씬 클 가능성이 크지만 이번에 계산하지 않았다. 청구가 실사용 CPU 기준인지 할당 기준인지 문서에서 확인하지 못했다.

## 4. 가장 큰 쟁점: vault와 qmd가 어디에 있나
지금은 **사용자 서버의 로컬 vault + 로컬 qmd 서버**가 전제다(자소서·프로필이 들어 있고, qmd 서버는 인증이 없다). AgentCore Runtime·Gateway는 AWS 안에서 돌아서 선택지가 갈린다.

| 안 | 구조 | 장점 | 걸리는 점 |
|---|---|---|---|
| **A. 백엔드는 사용자 서버, AgentCore는 일부만** | FastAPI·작업 큐·DB·qmd 전부 서버. Bedrock만 호출(지금과 같다). AgentCore는 Observability 등 일부 또는 안 씀 | 지금 코드 그대로, 가장 빠름, 비용 최소 | AgentCore를 거의 못 쓴다 |
| **B. Runtime에 파이프라인, 도구는 서버 vault에 연결** | Runtime이 파이프라인 실행, vault 검색은 서버의 qmd를 Gateway MCP 대상(또는 직접 HTTP)으로 호출 | AgentCore 핵심(Runtime·Gateway)을 쓴다. Quick도 같은 Gateway 도구를 쓴다 | 서버 qmd를 **인증을 붙여 외부에서 닿게** 해야 한다. Gateway가 사설 서버를 대상으로 삼을 수 있는지 [미확인] → 닿게 하려면 서버를 외부에 노출하는 문제(보안 정책 확인 필요). 서버 qmd에 API 키 인증 프록시가 필요 |
| **C. 코퍼스를 AWS로 옮김** | vault를 S3·EFS에 올리고 qmd나 Bedrock 지식 기반 등으로 검색. Runtime을 VPC에 붙여 접근 | 전부 AWS 안, 사설 접속 | **개인 vault(자소서·프로필)를 클라우드에 복제**, 검색 엔진·임베딩(qmd는 CPU로 돌면 느리다)을 새로 구성, 작업량이 큼 |

- [판단, 결정 전 의견] 데모 일정(10/23)을 고려하면 A를 기본으로 하고 B를 작은 범위로 시도하는 순서를 제안했으나, **사용자가 B로 가기로 했다(5절)**. B의 첫 관문 두 가지 중 qmd의 MCP 도구 형태는 확인했고, Gateway의 사설 대상 지원은 아직 [미확인]이다.
- [판단] 어느 안이든 지금 코드의 `tools/vault_search.search`가 이미 "HTTP(`MAS_QMD_URL`) 또는 CLI"를 고르는 구조라, 도구를 **Gateway MCP 클라이언트로 바꾸는 것은 `engines/strands/search_tool.py` 한 곳**에서 할 수 있다(core는 그대로).

## 5. 결정 (2026-10-05, 사용자)
**B로 간다.** AgentCore의 **Runtime·Gateway·Memory**를 모두 써 보고, vault의 qmd는 MCP 도구로 연결한다. 지금은 사용자 본인의 vault에 바로 접근하게 하고, 확장성은 급하지 않지만 열어 둔다(7절). 보안(인증)은 개인 프로젝트라 일단 넘어가되, 서버를 외부에 여는 문제는 운영 정책 확인이 필요하다.

### B 진행 순서 (제안, 작은 것부터 막히는 곳을 먼저 찾는다)
0. **[완료] qmd의 MCP 도구 형태 확인**(위 2.2).
1. **[구현·시험 완료 2026-10-05] qmd 앞에 얇은 MCP 프록시를 둔다(우리 코드, `src/modular_agentic/services/qmd_proxy/`).** 이유: ① `rerank:false`·`vec` 검색을 **강제**하는 `search_vault` 도구 하나만 노출(위 `rerank` 기본값 문제), ② qmd가 인증이 없어 API 키 같은 인증을 앞에 붙여야 외부에 열 수 있다(qmd README도 "put your own auth in front"), ③ 나중에 사용자별 범위 제한(RBAC)을 거는 자리가 된다(7절). [판단]
2. **[대기 — AWS 리소스를 만들어야 해서 사용자 확인 후] Gateway에 그 프록시를 MCP 서버 대상으로 등록**하고 Strands 에이전트의 `search_vault`를 Gateway MCP 클라이언트로 바꿔 본다. 코드는 `engines/strands/search_tool.py` 한 곳. **여기서 [미확인]이던 "Gateway가 사설 서버에 닿는가"가 결정된다.** 닿지 않으면 프록시를 외부에서 접근 가능하게 해야 하고, 운영 정책 확인이 필요하다. (AWS 리소스를 만드는 단계라 사용자 확인 후 진행.)
3. **Runtime에 파이프라인 올리기**(`BedrockAgentCoreApp` 진입점 얇게, `pipeline`·엔진 코드는 그대로). 비동기 작업은 `add_async_task`/`/ping` 규약. 결과 저장은 서버의 Postgres나 별도 저장소(Runtime 세션 저장소는 1GB 한도, 세션 격리라 영속 저장소로 부적합 [추정]).
4. **Memory 붙이기**(8절).
5. 각 단계마다 비용·지연·실패를 기록한다(Observability).

## 6. 아직 정해지지 않은 것
- DevCraft가 요구하는 AgentCore 사용 범위(담당자 확인 — 사용자가 이야기하기로 함).
- Gateway의 사설 대상 지원 여부(2단계 시험으로 판가름).
- Runtime 직접 코드 배포가 우리 의존성을 250MB 안에 담는지(배포 시 측정).

## 7. 확장성: RBAC·사용자별 지식베이스 (급하지 않음, 설계 방향만)
사용자 의견: 나중에 **RBAC(역할 기반 접근 제어, 누가 무엇을 볼 수 있는지 역할로 정함)**나 사용자별 시스템을 고려하면, ① 사용자가 **자기 지식베이스를 연결**하거나 ② **raw 파일을 올리면 AWS 안에서 qmd로 색인**해서 쓰는 확장이 열려 있었으면 좋겠다.

- **코퍼스 추상화.** 지금도 `tools/vault_search.search`가 "HTTP 서버 또는 CLI"를 고르는 구조다. 검색 대상을 **"코퍼스 제공자"** 한 단계로 올려 `사용자 → 검색 도구`를 돌려주게 하면 세 가지가 같은 인터페이스가 된다: (a) 지금의 로컬 qmd, (b) 사용자가 연결한 **자기 MCP 서버**(Gateway 대상으로 등록, 대상마다 자격증명을 따로 붙일 수 있다 — Gateway 개요 [확인]), (c) 업로드한 파일을 AWS 안에서 색인한 qmd. (a)는 이미 있고 (b)는 B 2단계의 연장이다.
- **업로드 → AWS 안 색인(c)**: 파일 → S3 → 색인 작업 → 사용자별 인덱스 → 검색. 문서에서 확인한 제약: Runtime 세션은 최대 2 vCPU/8GB, GPU 언급 없음, 세션 저장소 1GB. qmd 임베딩은 CPU로 돌면 느리다(이 서버 기준 vault 문서 21개 조각에 약 50초). 그래서 색인 작업은 Runtime 세션이 아니라 **별도 작업**(컨테이너 서비스 등)이 더 맞아 보인다 [추정, 시험하지 않음]. 대안으로 AWS 관리형 검색(Bedrock 지식 기반 등)도 있지만 이번에 조사하지 않았다.
- **RBAC가 걸리는 곳(조사 필요)**: Runtime·Gateway는 인바운드 인증으로 OAuth 2.0과 SigV4를 지원한다 [확인]. Gateway에는 "fine-grained access control" 페이지와 Policy(Cedar, 도구별 정책)가 문서 목차·한도 표에 있으나 **읽지 않았다**. qmd의 `collections` 인자로 컬렉션 단위 범위를 줄 수 있어, 프록시(5절 1)가 로그인한 사용자 → 허용 컬렉션 매핑을 강제하면 사용자별 지식베이스 분리의 기초가 된다 [판단].
- **Memory의 사용자 분리**: 한도 표에 "actor/session 단위" 요청 제한이 있어 사용자(actor)와 세션 구분이 있는 것은 보이나, 네임스페이스로 사용자 데이터를 격리하는 방법은 **읽지 않았다**.
- 그래서 지금 코드에 미리 해 둘 것은 하나다: 검색 도구·저장소 접근에 **사용자 식별자를 넘길 자리**(함수 인자)를 비워 두기. 나머지는 필요해질 때 한다.

## 8. Memory: 쓰되 앞으로 어떻게 발전시킬 수 있나
**원칙(내 제안)**: vault가 **경험의 사실**의 원본이다. Memory에는 **과정에 대한 지식**(선호, 이력, 이미 지적된 서술)만 둔다. 사실을 Memory에 복제하면 vault와 어긋나거나 낡을 수 있고, 그건 우리가 검증으로 막으려는 바로 그 문제다.

| 단계 | 쓰임 | 설명 | 상태 |
|---|---|---|---|
| **1. 지침·피드백 저장** | 단기: 피드백 이벤트(선택·코멘트) 저장. 장기: user preference 전략이 선호를 추출 | 지금 `logs/feedback`·`logs/guidance.md`의 파일을 대체. 추출 결과는 **후보**로만 받고 사용자가 승인해야 지침이 된다(지금 설계 유지) | 먼저 붙일 것 |
| **2. 회사별 지원 이력** | 같은 회사 재지원 때 이전 자소서·라운드에서 걸린 TODO를 꺼내 쓴다 | vault 설계에 이미 있는 항목(이전에 사람이 수동으로 하던 것). 요약·episodic 전략이 후보 | 다음 |
| **3. "이미 지적된 서술" 기억** | Verifier가 플래그한 서술(예: "연동만 했다", "인턴 1개월")을 저장해 다음 초안에서 같은 과장을 미리 피한다 | 사용자 피드백 + 검증 결과를 함께 저장. 같은 실수를 반복하지 않게 하는 쪽이라 효과를 비교하기 쉽다 | 유망 |
| **4. 각도 선호 학습** | 어떤 각도의 초안이 선택됐는지(episodic)를 쌓아 다음 초안의 각도를 고른다 | 지침 문장보다 한 단계 나아간 self-improving. 표본이 쌓여야 의미가 있다 | 나중 |
| **5. 사용자별 분리** | actor별 메모리로 다중 사용자 지원 | 7절과 이어진다 | 확장 때 |

- 공통 위험 [판단]: ① **낡은 기억**(예전에 맞던 선호·사실이 지금은 틀림), ② **기억끼리 모순**, ③ 비동기 추출 지연(장기 메모리는 이벤트 저장 뒤 비동기로 추출된다 [확인]). 대응은 승인 단계 유지, 기억에 날짜 표시, 사실은 항상 vault로 재확인.
- **효과를 어떻게 알까**: 1단계라면 "Memory 지침을 쓴 초안 vs 파일 지침을 쓴 초안"은 내용이 같아 비교 의미가 없다. 비교가 의미 있는 것은 3·4단계다. 그래서 1단계는 "AgentCore Memory를 실제로 써 봄"이 목적이고, 개선 효과의 검증은 3단계부터 시나리오로 설계하는 편이 맞다.
- 비용: 장기 저장 $0.75/1,000레코드·월, 검색 $0.50/1,000회 등 [확인, 3절]. 우리 규모에서는 작을 것으로 보이나 계산하지 않았다.

## 9. 사용자가 정해야 할 것 (남은 것)
1. B 2단계에서 **AWS 리소스(Gateway)를 만드는 것**을 진행해도 되는지(비용은 작지만 사용자 계정에 리소스가 생긴다).
2. 프록시를 **외부에 노출**해야 할 때 서버 정책(Gateway가 사설 대상을 못 닿는 경우).
3. Memory를 1단계(지침·피드백)부터 붙이는 것에 동의하는지.

1. AgentCore를 **어디까지** 쓸지(Runtime만 / +Gateway / +Memory). DevCraft가 요구하는 범위는 담당자 확인 필요.
2. vault·qmd 위치 안(A/B/C). 서버를 외부에 열어도 되는지(B) 확인.
3. Memory를 "지침·이력 저장"에 쓸지, 쓴다면 자동 추출 후보 + 사용자 승인 구조로 갈지.

## 6. 출처 (모두 직접 열어 읽음)
- Runtime 장시간·비동기: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-long-run.html
- Runtime 서비스 규약: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
- Runtime 직접 코드 배포: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy.html
- VPC 연결·지원 지역: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html
- 한도: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html
- Gateway 개요·대상: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html , https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-supported-targets.html , https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-MCPservers.html
- Memory: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
- 가격: https://aws.amazon.com/bedrock/agentcore/pricing/
- (검색으로 얻은 개요, 원문 확인은 위 공식 문서로 함) https://aws.amazon.com/bedrock/agentcore/faqs/

## 10. 프록시 구현 결과 (2026-10-05)
- **노출 도구**: 읽기 9개(`search_vault`, `query`, `get`, `multi_get`, `status`, `ls`, `collection_list`, `collection_show`, `context_list`)와 관리 10개(`update`, `embed`, `cleanup`, `collection_add`/`remove`/`rename`/`include`/`exclude`, `context_add`/`remove`). 관리 도구는 admin 키와 서버 전역 스위치(`allow_admin_tools`)가 모두 있어야 보인다(기본 꺼짐).
- **일부러 안 연 것**: `collection update-cmd`(qmd README: `qmd update` 때 실행되는 bash 명령을 설정 → 열면 서버에서 임의 명령 실행), `init`, `trust`, `bench`, `skills`, `--index`. `collection_add`는 서버 설정의 허용 루트 폴더 아래(심볼릭 링크를 푼 실제 경로 기준)만 받는다. 사용자가 "qmd 명령 전부"를 원했지만 이 둘은 위험이 커서 선을 그었다.
- **프록시가 막는 것**: 모르는 인자(qmd는 조용히 무시, 예: `collection` 단수형), 리랭킹(서버 설정이 허용하지 않으면 항상 끔), 명령줄 값의 옵션 주입(`-`로 시작하는 값·형식이 틀린 이름), 인증 없는 호출(401), 허용되지 않은 브라우저 Origin(403).
- **시험으로 찾은 결함**: 컬렉션 이름 정규식이 `-`로 시작하는 값(`--help`)을 허용했다. 단위 테스트가 잡았고 고쳤다.
- **시험 범위**: 단위 테스트(가짜 qmd·가짜 CLI) + 공식 MCP 클라이언트 SDK(mcp 2.1.1)로 프록시와 대화 + **실제 qmd를 뒤에 두고** `search_vault`, `get`, `multi_get`, `status`, `ls`, `collection_list`를 호출했고, 모르는 인자 거부·reader에게 관리 도구가 안 보임·키 없음/틀린 키 401을 확인했다. `search_vault`는 실제 vault에서 `FINT.md`를 1위로 돌려줬다. **관리 도구를 실제 qmd로는 시험하지 않았다**(색인을 바꾸므로). 부하·동시 요청, TLS, Gateway 연결은 시험하지 않았다.
- **qmd의 `get` 응답 형식**: 텍스트가 아니라 `EmbeddedResource`(문서 리소스)로 오고 프록시가 그대로 전달한다. Strands의 MCP 클라이언트가 이 형식을 읽는지는 연결할 때 확인해야 한다.
- **검색 도구의 MCP 방식(2026-10-05)**: `tools/vault_search.search`에 `mcp` 방식을 추가했다(`MAS_MCP_URL`·`MAS_MCP_KEY`·`MAS_MCP_TOOL`). Strands의 `search_vault` 도구와 매퍼의 인용 검증은 그대로 두고 데이터 경로만 프록시를 거친다(에이전트가 MCP 도구를 직접 부르게 하면 "인용한 근거가 실제 검색 결과였는가" 검증이 깨지기 때문). 실제 qmd를 뒤에 둔 시험에서 같은 질의의 HTTP 결과와 MCP(프록시 경유) 결과가 경로·줄·발췌까지 동일했다(1개 질의, 5건). MCP 호출 코드는 `tools/mcp_http.py`로 모아 프록시 백엔드와 같이 쓴다. 이 클라이언트는 `initialize` 없이 `tools/call` 하나만 보내는 **무상태 서버 전용**이라, **Gateway에 연결할 때 `initialize`·세션·도구 이름 접두어(`<대상>___search_vault`)·인증(OAuth) 요구에 맞는지 확인해야 한다.**
- 프록시의 결과 개수 기본 상한을 20에서 50으로 올렸다(`vault_search`가 n×4건, 최소 20건을 요청하고 매퍼 기본 n=8이면 32건이라 20이면 거부된다).
- **Strands 매핑을 프록시 경유로 실제 실행(2026-10-05, Bedrock, 요구 3 하나, 1회)**: `검색 방식: mcp http://127.0.0.1:18282/mcp`로 시작해 분석 → 매핑이 끝까지 돌았다(매핑 14.9초, 모델 응답 10.3초, 토큰 입력 23,886/출력 715, 루프 4회). 프록시 감사 로그에 `search_vault` 호출 4건(모두 성공)이 남아 **에이전트의 검색 4번이 전부 프록시를 거쳤음**을 확인했다. 판단은 `확인`, 근거 4건, 인용 검증도 정상 동작(검색 결과에 있던 경로만 인용). 앞선 직접 HTTP 실행(요구 3, 매핑 14.2초, 입력 24,123/출력 665, 판단 `확인`)과 시간·토큰·판단이 비슷하지만, 그 사이 vault가 바뀌어(AgentCore 페이지 등) 근거 목록은 같지 않아 **결과 동일성은 검증하지 못했다**(표본 1). 검색 호출 시간(합 5.8초)은 프록시가 아니라 방금 켠 qmd 서버의 모델 적재(처음 두 호출 약 2.7초)가 대부분이다. 근거 4건 중 3건이 vault의 시스템 상태·기록 페이지였다. 이건 그 자체로 문제가 아니다(실제 경험의 기록이고 구현은 사실이다). 다만 상태 기록을 영구 사실로 읽는 것, 평가 오염, 검증의 독립성은 별개의 한계로 남는다(실험 002 11절 정정).

## 11. 서버에 닿는 길: Tailscale 브리지 (2026-10-06 결정, 시험 준비 완료·미실행)
### 문제와 결정
사용자 서버(vault·qmd 프록시)는 인터넷에 열려 있지 않고 **Tailscale 사설 통로(tailnet)**로만 닿는다. Gateway는 AWS 관리형이라 Tailscale을 설치할 수 없다. 사용자가 서버를 외부에 여는 것은 망설여서(Funnel 같은 공개 노출은 보류), **Runtime에 호스팅한 "브리지"**(우리가 만들 작은 MCP 서버, AWS 서비스가 아니다)가 Tailscale로 서버 프록시에 닿고 Gateway는 브리지를 부르는 구조로 가기로 했다.
```
[Runtime ① 파이프라인] → [Gateway] → [Runtime ② 브리지 + Tailscale] —(tailnet)→ [서버: 프록시 → qmd]
```
홉이 늘어나는 것을 알고 정했다(이유: 같은 Gateway 입구를 Quick 등 다른 클라이언트도 쓰게 하려는 목적). 대안은 Runtime ①에서 직접 tailnet으로 서버에 가는 짧은 구조(Gateway·브리지 없음)이고, 어느 쪽이든 아래 시험이 먼저다.
### 문서로 확인한 것 (2026-10-06)
- Tailscale: **userspace networking** 모드는 `/dev/net/tun`이 없는 컨테이너·서버리스에서 쓰는 모드로, `tailscaled`가 SOCKS5(또는 HTTP) 프록시가 되어 같은 컨테이너의 프로그램이 그걸 거쳐 tailnet 기기에 접속한다(HTTP 프록시는 HTTP·HTTPS만, SOCKS5는 모든 트래픽). **Funnel**은 tailnet 서비스를 공개 인터넷에 노출한다(443·8443·10000, TLS만, `funnel` ACL 속성 필요).
- AgentCore Runtime이 MCP 서버를 호스팅하는 조건: ARM64 컨테이너, 0.0.0.0:8000, `POST /mcp`, **무상태 streamable-http**, 플랫폼이 붙이는 `Mcp-Session-Id` 헤더를 거부하지 않을 것. Gateway는 Runtime에 호스팅된 MCP 서버를 **IAM(SigV4) 인증**으로 부를 수 있다(MCP 서버 대상 문서). 우리 프록시는 무상태이고 세션 id를 무시해 조건에 맞는다(구현에서 확인한 것은 아니고 문서 조건과의 대조).
- 직접 코드 배포(zip): Python 3.10~3.14(Amazon Linux 2023, **arm64**), zip 압축 250MB·풀면 750MB 이하, 실행 파일·디렉터리 755·그 밖 644, 진입점 `main.py`, `/var/task`에 풀림. 필요한 것은 S3 버킷, 실행 역할(IAM), Runtime.
### 아직 모르는 것 (시험으로 확인할 것)
**AgentCore Runtime의 microVM에서 `tailscaled`(userspace)가 실제로 도는가.** 외부 인터넷(Tailscale 제어 서버·중계 서버 접속) 가능 여부, 쓰기 가능한 경로(`/tmp`), UDP 가능 여부도 문서에서 확인하지 못했다. Python의 표준 `urllib`은 SOCKS를 못 써서 브리지를 만들 때는 SOCKS5 클라이언트 구현(이미 시험 프로그램에 표준 라이브러리로 구현)이나 라이브러리가 필요하다.
### 준비한 것 (로컬, AWS 호출 없음)
`deploy/tailscale_probe/`: `main.py`(Runtime 규약 `/ping`·`/invocations`를 구현하고 환경·인터넷·tailscaled 기동·tailnet 가입·상태·ping·SOCKS5 HTTP를 단계별로 점검해 JSON으로 돌려줌, 실패해도 다음 단계 계속, 키는 결과에 안 남김), `build.sh`(Tailscale 공식 ARM64 정적 바이너리 1.102.4 + main.py → 34MB zip, 권한 755/644 확인, 바이너리 `ARM aarch64` 확인), `deploy_probe.py`(plan/deploy/invoke/cleanup, 실행 역할 정책은 공식 문서의 direct deploy 실행 역할 + zip 읽기 S3 권한). 단위 테스트는 가짜 SOCKS 서버로 접속·거절·키 가림·규약을 확인(Tailscale·AWS 없이).
### 시험을 하려면 사용자에게 필요한 것
1. Tailscale 관리 콘솔(Keys 페이지)에서 **한 번 쓰는(one-off) 임시(ephemeral) 인증 키** 만들기. **태그는 필수가 아니다**(공식 문서: 인증 키는 키를 만든 사용자의 신원으로 기기를 인증하고 태그는 선택). 만료는 1~90일이라 **최소 1일**이다(2026-10-06 정정: 앞서 "1시간"이라고 적은 것은 기억에 의존한 틀린 말이었다). 키는 서버의 파일(권한 600)에 저장(내가 읽지 않는다). 태그와 ACL 규칙은 "브리지만 서버의 8282 포트에 닿게" 접근을 좁히고 싶을 때만 필요하고, 시험에는 필요 없다. 태그 없는 시험 기기는 사용자의 신원으로 가입하므로 tailnet 정책(ACL)이 허용하는 만큼 접근할 수 있다(정책이 기본값이면 사용자 기기들과 같은 범위인지는 문서에서 확인하지 못했다). 시험 뒤 키를 폐기하고 기기를 지운다.
2. AWS 계정(IAM 사용자 `user`)에 BedrockAgentCoreFullAccess와 역할·버킷을 만들 권한(`iam:CreateRole`·`PutRolePolicy`·`PassRole`, `s3:CreateBucket`)이 있는지 — 확인하지 못했다(vault에는 `AmazonBedrockFullAccess`만 적혀 있고 AgentCore 권한은 별도일 수 있다).
3. 서버에서 qmd와 프록시를 tailnet 주소에 묶어 켜 두는 것(시험 때 내가 스크립트로 켠다).

### 시험 결과 (2026-10-06, Runtime 1개, 점검 호출 1회) — **Runtime 안에서 Tailscale이 돈다**
계정 권한을 추가한 뒤(관리형 `BedrockAgentCoreFullAccess` + S3 버킷·IAM 역할 인라인 정책, 아래 "배포 중 만난 것") `deploy_probe.py deploy`로 Runtime(`mas_tailscale_probe`, PYTHON_3_13 직접 코드 배포, PUBLIC 네트워크)을 만들고, 서버에서 qmd와 프록시를 tailnet 주소(`100.64.0.1:8282`)에만 묶어 켠 채 `invoke`했다.
| 단계 | 결과 |
|---|---|
| env | Amazon Linux 2023 **aarch64**(커널 6.1.161), **uid 991(root 아님)**, Python 3.13.9, `/var/task`, `/tmp` 쓰기 가능, **`/dev/net/tun` 없음**(그래서 userspace 모드가 필요), 메모리 8GB, 바이너리 실행 가능 |
| internet | `controlplane.tailscale.com` DNS·HTTPS 200 — **외부 HTTPS가 나간다**(PUBLIC 네트워크) |
| tailscaled_start | 0.2초, 성공(`--tun=userspace-networking`) |
| tailscale_up | **4.3초**, 성공. 태그 없는 일회용·임시 키로 가입. 호스트명 `agentcore-probe`, tailnet 주소 `100.64.0.2`, 상태 `Running`, 피어 4개(온라인 2) |
| tailscale_ping | 서버(`server-host`)에서 pong 3번, **DERP 중계(도쿄)로 134~332ms**. 종료 코드는 1이었다(원인 미확인, 직접 경로를 못 만든 것으로 짐작) |
| socks_http | **`HTTP/1.1 200 OK` `{"status":"ok"}`**, 0.41초. 서버 프록시 로그에 `100.64.0.2`(Runtime)에서 온 `GET /health 200`이 남았다 |
**의미**: Runtime microVM 안에서 `tailscaled`(userspace)가 기동해 tailnet에 가입하고, SOCKS5를 거쳐 서버의 프록시에 닿는다. "서버를 인터넷에 열지 않고 Runtime이 사설 통로로 닿는다"는 브리지 구조의 핵심 전제가 성립한다.
**짚어 둘 것**
- **UDP가 막혀 있다**(tailscaled 로그: `UDP is blocked, trying HTTPS`). 직접 연결은 못 만들고 항상 DERP 중계를 거친다. 지연은 요청 한 번당 0.1~0.3초대였고, MCP 호출 한 번에 여러 왕복이 들면 더해진다. 처리량·지속 부하·MCP 호출 전체 지연은 시험하지 않았다(1회).
- **일회용 키 문제.** 시험에는 일회용(one-off) 키를 썼다. **새 microVM 세션마다 `tailscale up`이 다시 필요**해서(매번 약 4.3초) 브리지에는 일회용 키가 맞지 않는다. 재사용 가능(reusable) 키는 공식 문서가 "도난되면 위험하다"고 경고하는 종류라, 브리지에서는 키 보관(예: Secrets Manager)·만료 주기·접근 범위(태그와 ACL) 설계가 필요하다. 이건 아직 정하지 않았다.
- 점검 호출 시간 전체(microVM 콜드 스타트 포함)와 세션이 끊긴 뒤 재가입 동작은 측정하지 않았다. 여기서는 Python의 `urllib`이 아니라 시험 프로그램의 표준 라이브러리 SOCKS5 클라이언트로 접속했다.
### 배포 중 만난 것 (권한·스크립트)
- 계정 `user`에는 처음에 S3 버킷 생성 권한이 없었다. `BedrockAgentCoreFullAccess`는 AgentCore 동작·`iam:PassRole`(`*BedrockAgentCore*` 역할)·S3 읽기·`bedrock-agentcore-runtime-*` 버킷 쓰기만 주고 **IAM 역할 생성·삭제와 S3 삭제는 없다**(2026-10-06 정책 페이지로 확인). 버킷 하나와 역할 하나로 범위를 좁힌 인라인 정책을 추가했다.
- 스크립트 실수 두 가지를 고쳤다: IAM 역할 설명(Description)에 한글을 넣으면 거부된다(라틴 문자만), 역할을 막 만들면 Runtime 생성이 "역할을 확인하지 못했다"로 잠시 실패하는데 재시도 조건이 그 문구("assumption")를 놓쳤다.
