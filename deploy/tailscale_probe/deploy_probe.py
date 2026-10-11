"""Tailscale 시험용 Runtime을 AWS에 만들고, 호출하고, 지운다. **AWS 리소스를 만드는 스크립트라 사용자 확인 뒤에만 실행한다.**

사용 (프로필은 AWS_PROFILE, 리전 기본 us-west-2)
  uv run --extra strands python deploy/tailscale_probe/deploy_probe.py plan      # AWS를 부르지 않고 만들 것만 보여 준다
  uv run --extra strands python deploy/tailscale_probe/deploy_probe.py deploy --authkey-file ~/.config/mas/ts_authkey --target 100.64.0.1:8282
  uv run --extra strands python deploy/tailscale_probe/deploy_probe.py invoke    # 단계별 결과(JSON)를 출력
  uv run --extra strands python deploy/tailscale_probe/deploy_probe.py cleanup   # 만든 것을 모두 지운다(Runtime, S3 파일·버킷, 역할)

만드는 AWS 리소스(전부 cleanup으로 지운다)
  S3 버킷  bedrock-agentcore-code-<계정>-<리전>   배포 zip을 올린다(이미 있으면 재사용하고 cleanup이 버킷은 지우지 않는다)
  IAM 역할  AmazonBedrockAgentCoreMasProbeRole    Runtime 실행 역할(공식 문서의 direct deploy 권한 + zip을 읽는 S3 권한)
  Runtime  mas_tailscale_probe                    코드 배포(PYTHON_3_13, ARM64), 네트워크 PUBLIC
이 계정(IAM 사용자)에는 BedrockAgentCoreFullAccess와 위 역할·버킷을 만들 권한(iam:CreateRole·PutRolePolicy·PassRole, s3:CreateBucket)이 필요하다.
Tailscale 인증 키: 파일에서만 읽고 출력하지 않는다. Runtime 환경변수(TS_AUTHKEY)로 들어가므로 **한 번만 쓰는(one-off) 임시(ephemeral) 키**를 권한다. 만료는 1~90일 중 고르며(공식 문서) 가장 짧은 1일이 최소다. 태그는 없어도 된다(키를 만든 사용자의 신원으로 가입한다). 시험이 끝나면 콘솔에서 키를 폐기하고 기기를 지운다.
상태(ARN·ID)는 같은 폴더의 .probe-state.json에 저장한다(git 제외).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".probe-state.json"
ZIP_PATH = HERE / "deployment_package.zip"
ROLE_NAME = "AmazonBedrockAgentCoreMasProbeRole"  # 사용자 권한의 PassRole 패턴(AmazonBedrockAgentCore*)에 맞춘 이름
RUNTIME_NAME = "mas_tailscale_probe"
POLICY_NAME = "MasProbeExecution"


def trust_policy(account: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AssumeRolePolicy", "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
            },
        }],
    }  # fmt: skip


def execution_policy(account: str, region: str, bucket: str) -> dict:
    """공식 문서(runtime-permissions)의 direct deploy 실행 역할에서 이 시험에 필요한 것 + zip을 읽는 S3 권한. 모델 호출 권한은 뺐다."""
    logs = f"arn:aws:logs:{region}:{account}:log-group"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/*"]},
            {"Effect": "Allow", "Action": ["logs:PutResourcePolicy"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/{RUNTIME_NAME}-*"]},
            {"Effect": "Allow", "Action": ["logs:DescribeLogGroups"], "Resource": [f"{logs}:*"]},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": [f"{logs}:/aws/bedrock-agentcore/runtimes/*:log-stream:*"]},
            {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], "Resource": ["*"]},
            {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*", "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}}},
            {"Sid": "ReadCodePackage", "Effect": "Allow", "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{bucket}/{RUNTIME_NAME}/*"]},
        ],
    }  # fmt: skip


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def clients(region: str):
    import boto3

    session = boto3.Session(region_name=region)
    return {
        "sts": session.client("sts"),
        "s3": session.client("s3"),
        "iam": session.client("iam"),
        "control": session.client("bedrock-agentcore-control"),
        "data": session.client("bedrock-agentcore"),
    }


def cmd_plan(args: argparse.Namespace) -> None:
    print("AWS를 부르지 않는다. deploy를 실행하면 아래를 만든다.\n")
    print(f"  리전: {args.region}  프로필: {os.environ.get('AWS_PROFILE', '(기본)')}")
    print("  S3 버킷  bedrock-agentcore-code-<계정>-<리전> (없으면 만든다. 배포 zip 약 36MB)")
    print(f"  IAM 역할 {ROLE_NAME} (신뢰: bedrock-agentcore, 권한: CloudWatch Logs·X-Ray·지표 + zip 읽기)")
    print(f"  Runtime  {RUNTIME_NAME} (PYTHON_3_13, PUBLIC 네트워크, 환경변수: TS_AUTHKEY·PROBE_TARGET·PROBE_HOSTNAME)")
    print(f"\n  zip 파일: {ZIP_PATH} ({'있음' if ZIP_PATH.exists() else '없음 — build.sh를 먼저 실행'})")
    print("  비용: 이 시험 규모에서는 거의 없다(Runtime은 세션이 도는 시간만큼, 몇 분). 추정이고 청구 기준은 확인하지 못했다.")
    print("  지우기: cleanup (Runtime, S3 파일, 역할. 버킷은 우리가 만들었을 때만).")


def cmd_deploy(args: argparse.Namespace) -> None:
    if not ZIP_PATH.exists():
        sys.exit("deployment_package.zip이 없다. 먼저 deploy/tailscale_probe/build.sh를 실행한다.")
    key = Path(args.authkey_file).expanduser().read_text(encoding="utf-8").strip() if args.authkey_file else os.environ.get("TS_AUTHKEY", "")
    if not key.startswith("tskey-"):
        sys.exit("Tailscale 인증 키(tskey-…)가 없다. --authkey-file로 키가 든 파일(권한 600)을 준다.")
    c = clients(args.region)
    account = c["sts"].get_caller_identity()["Account"]
    bucket = f"bedrock-agentcore-code-{account}-{args.region}"
    state = load_state()
    state.update(account=account, region=args.region, bucket=bucket)

    # 1) S3 버킷
    try:
        c["s3"].head_bucket(Bucket=bucket)
        print(f"S3 버킷이 이미 있다: {bucket}")
    except Exception:  # noqa: BLE001 (없거나 권한 없음 — 만들어 보고 안 되면 그 오류가 나온다)
        c["s3"].create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": args.region})
        state["bucket_created"] = True
        print(f"S3 버킷을 만들었다: {bucket}")
    save_state(state)
    c["s3"].upload_file(str(ZIP_PATH), bucket, f"{RUNTIME_NAME}/deployment_package.zip", ExtraArgs={"ExpectedBucketOwner": account})
    print("zip을 올렸다")

    # 2) 실행 역할
    role_arn = f"arn:aws:iam::{account}:role/{ROLE_NAME}"
    try:
        c["iam"].create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust_policy(account, args.region)), Description="mas tailscale probe (temporary, removed by cleanup)")  # IAM 설명은 한글을 받지 않는다(라틴 문자만)
        state["role_created"] = True
        print(f"역할을 만들었다: {ROLE_NAME}")
    except c["iam"].exceptions.EntityAlreadyExistsException:
        print(f"역할이 이미 있다: {ROLE_NAME}")
    c["iam"].put_role_policy(RoleName=ROLE_NAME, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(execution_policy(account, args.region, bucket)))
    save_state(state)

    # 3) Runtime (역할 전파를 기다리며 재시도)
    env = {"TS_AUTHKEY": key, "PROBE_HOSTNAME": args.hostname}
    if args.target:
        env["PROBE_TARGET"] = args.target
    if args.tags:
        env["TS_TAGS"] = args.tags
    artifact = {"codeConfiguration": {"code": {"s3": {"bucket": bucket, "prefix": f"{RUNTIME_NAME}/deployment_package.zip"}}, "runtime": "PYTHON_3_13", "entryPoint": ["main.py"]}}
    for attempt in range(12):
        try:
            created = c["control"].create_agent_runtime(
                agentRuntimeName=RUNTIME_NAME,
                agentRuntimeArtifact=artifact,
                networkConfiguration={"networkMode": "PUBLIC"},
                roleArn=role_arn,
                environmentVariables=env,
                lifecycleConfiguration={"idleRuntimeSessionTimeout": 600, "maxLifetime": 1800},
            )
            break
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if ("role validation" in message or "trust policy" in message or "assum" in message) and attempt < 11:  # 방금 만든 역할이 아직 퍼지지 않은 경우
                print("역할 전파를 기다리는 중…")
                time.sleep(10)
                continue
            raise
    state.update(runtime_id=created["agentRuntimeId"], runtime_arn=created["agentRuntimeArn"])
    save_state(state)
    print(f"Runtime을 만들었다: {created['agentRuntimeId']} — READY를 기다린다")
    for _ in range(60):
        status = c["control"].get_agent_runtime(agentRuntimeId=created["agentRuntimeId"])["status"]
        print(f"  상태: {status}")
        if status in ("READY", "CREATE_FAILED", "UPDATE_FAILED"):
            break
        time.sleep(10)


def cmd_invoke(args: argparse.Namespace) -> None:
    state = load_state()
    if "runtime_arn" not in state:
        sys.exit("Runtime이 없다. 먼저 deploy를 실행한다.")
    c = clients(state["region"])
    payload = {"steps": args.steps.split(",")} if args.steps else {}
    session_id = state.get("session_id") or f"mas-probe-{uuid.uuid4()}"  # 세션 id는 33자 이상
    state["session_id"] = session_id
    save_state(state)
    response = c["data"].invoke_agent_runtime(
        agentRuntimeArn=state["runtime_arn"], runtimeSessionId=session_id, qualifier="DEFAULT", payload=json.dumps(payload).encode("utf-8")
    )
    body = response["response"].read().decode("utf-8", errors="replace")
    try:
        print(json.dumps(json.loads(body), ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(body)


def cmd_cleanup(args: argparse.Namespace) -> None:
    state = load_state()
    if not state:
        sys.exit("지울 것이 없다(.probe-state.json 없음).")
    c = clients(state["region"])
    if state.get("runtime_id"):
        try:
            c["control"].delete_agent_runtime(agentRuntimeId=state["runtime_id"])
            print("Runtime 삭제 요청")
        except Exception as exc:  # noqa: BLE001
            print(f"Runtime 삭제 실패: {exc}")
    bucket = state.get("bucket")
    if bucket:
        try:
            c["s3"].delete_object(Bucket=bucket, Key=f"{RUNTIME_NAME}/deployment_package.zip")
            print("S3의 zip 삭제")
            if state.get("bucket_created"):
                c["s3"].delete_bucket(Bucket=bucket)
                print("S3 버킷 삭제(우리가 만든 것)")
        except Exception as exc:  # noqa: BLE001
            print(f"S3 정리 실패: {exc}")
    if state.get("role_created"):
        try:
            c["iam"].delete_role_policy(RoleName=ROLE_NAME, PolicyName=POLICY_NAME)
            c["iam"].delete_role(RoleName=ROLE_NAME)
            print("역할 삭제")
        except Exception as exc:  # noqa: BLE001
            print(f"역할 삭제 실패: {exc}")
    STATE_FILE.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default="us-west-2")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan").set_defaults(func=cmd_plan)
    dp = sub.add_parser("deploy")
    dp.add_argument("--authkey-file", help="Tailscale 인증 키(tskey-…)가 든 파일 경로(권한 600)")
    dp.add_argument("--target", help="서버 프록시의 tailnet 주소 host:port (예: 100.64.0.1:8282)")
    dp.add_argument("--hostname", default="agentcore-probe")
    dp.add_argument("--tags", help="인증 키에 붙은 태그를 광고할 때(예: tag:agentcore-probe)")
    dp.set_defaults(func=cmd_deploy)
    iv = sub.add_parser("invoke")
    iv.add_argument("--steps", help="일부 단계만(쉼표로). 기본은 전부")
    iv.set_defaults(func=cmd_invoke)
    sub.add_parser("cleanup").set_defaults(func=cmd_cleanup)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
