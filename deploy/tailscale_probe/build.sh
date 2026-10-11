#!/usr/bin/env bash
# AgentCore Runtime 직접 코드 배포(zip)용 패키지를 만든다: main.py + Tailscale ARM64 정적 바이너리.
# AWS는 부르지 않는다. 인터넷에서 Tailscale 공식 패키지(약 36MB)를 내려받는다. 결과: deployment_package.zip
#
# Runtime은 ARM64(Amazon Linux 2023)다. 이 서버는 x86이지만 여기서 만드는 것은 파일 묶음이라 교차 빌드가 필요 없다
# (바이너리는 미리 만들어진 ARM64용을 받는다). Python 의존성은 없다(main.py는 표준 라이브러리만 쓴다).
# 권한: Runtime은 디렉터리와 실행 파일에 755, 그 밖의 파일에 644를 요구한다(공식 문서).
set -euo pipefail
cd "$(dirname "$0")"
VERSION="${TS_VERSION:-1.102.4}"
URL="https://pkgs.tailscale.com/stable/tailscale_${VERSION}_arm64.tgz"

rm -rf build deployment_package.zip
mkdir -p build/bin
curl -fsSL -o build/ts.tgz "$URL"
tar -xzf build/ts.tgz -C build --strip-components=1 "tailscale_${VERSION}_arm64/tailscale" "tailscale_${VERSION}_arm64/tailscaled"
mv build/tailscale build/tailscaled build/bin/
rm build/ts.tgz
cp main.py build/main.py

find build -type d -exec chmod 755 {} +
chmod 755 build/bin/tailscale build/bin/tailscaled
chmod 644 build/main.py
(cd build && zip -qr ../deployment_package.zip .)

echo "만든 파일: $(pwd)/deployment_package.zip ($(du -h deployment_package.zip | cut -f1), 한도 압축 250MB·풀면 750MB)"
echo "바이너리 구조:"; file build/bin/tailscaled | sed 's/,.*stripped//' | cut -c1-140
