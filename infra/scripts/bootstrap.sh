#!/usr/bin/env bash
# 1단계 서버 초기 설정: 방화벽, 커널 값, swap.
# 여러 번 돌려도 결과가 같도록 썼다. 관리자 IP는 바뀔 수 있어서 레포에 적지 않고 인자로 받는다.
#   사용법: sudo ./bootstrap.sh <kubectl을 쓸 노트북의 공인 IP>
set -euo pipefail

ADMIN_IP="${1:?사용법: sudo ./bootstrap.sh <kubectl을 쓸 노트북의 공인 IP>}"
if [ "$(id -u)" -ne 0 ]; then
  echo "sudo로 실행해야 한다" >&2
  exit 1
fi

# --- 방화벽 -----------------------------------------------------------------
# enable 전에 22를 먼저 열어야 지금 SSH 접속이 끊기지 않는다.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
# 6443(쿠버네티스 API)은 관리자 IP만. 인터넷 전체에 열면 누구나 API 인증을 시도할 수 있다.
ufw allow from "$ADMIN_IP" to any port 6443 proto tcp
# k3s 공식 요구사항: ufw를 켜 둘 거면 파드 대역과 서비스 대역을 열어야 한다.
# 안 열면 파드가 서비스 IP로 API 서버나 다른 파드에 닿지 못한다(k3s 기본 CIDR 기준).
ufw allow from 10.42.0.0/16 to any
ufw allow from 10.43.0.0/16 to any
ufw --force enable

# --- vm.max_map_count -------------------------------------------------------
# OpenSearch는 262144 이상이 필요하다. 이미 더 큰 값이면(Ubuntu 24.04 기본 1048576) 낮추지 않는다.
# /etc/sysctl.d/99-*.conf는 마지막에 읽혀서 재부팅 뒤에도 이 값이 남는다.
current="$(sysctl -n vm.max_map_count)"
target=262144
if [ "$current" -gt "$target" ]; then
  target="$current"
fi
echo "vm.max_map_count=${target}" > /etc/sysctl.d/99-opensearch.conf
sysctl --system > /dev/null

# --- swap -------------------------------------------------------------------
# kubelet은 기본 설정에서 swap을 허용하지 않고, 메모리 requests와 limits 계산도 swap이 있으면 어긋난다.
swapoff -a
# 재부팅 뒤에도 꺼져 있게 fstab의 swap 줄을 주석 처리한다(이미 주석이면 그대로 둔다).
sed -i.bak -E '/^[^#].*[[:space:]]swap[[:space:]]/ s/^/#/' /etc/fstab

# --- 결과 확인 --------------------------------------------------------------
ufw status
sysctl vm.max_map_count
free -h | grep -i swap
