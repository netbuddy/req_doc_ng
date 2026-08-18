#!/usr/bin/env bash
# 离线发布脚本公共函数：日志、指纹、环境检查、manifest 读取。
# 由 pack-full.sh（开发机）与 install-full.sh / verify-release.sh / status.sh / backup-db.sh（离线机）共用。
# 约定：所有脚本 set -euo pipefail；本文件只定义函数与常量，不产生副作用。

set -euo pipefail

REQDOC_LOG_PREFIX="${REQDOC_LOG_PREFIX:-reqdoc}"

log()  { printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$REQDOC_LOG_PREFIX" "$*" >&2; }
warn() { printf '[%s] [%s] 警告：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$REQDOC_LOG_PREFIX" "$*" >&2; }
die()  { printf '[%s] [%s] 失败：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$REQDOC_LOG_PREFIX" "$*" >&2; exit 1; }

# 步骤分隔线：安装过程日志按步骤可读。
step() { printf '\n===== %s =====\n' "$*" >&2; }

# ---------------------------------------------------------------- 指纹与校验 --

sha256_of_file() { sha256sum "$1" | awk '{print $1}'; }

# 目录整体指纹：目录内全部普通文件按相对路径排序后，逐文件 "sha256  相对路径" 汇总再取一次 sha256。
# 文件增删改任一发生都会改变结果；文件时间戳、属主不影响（只认内容与路径）。
dir_digest() {
  local dir="$1"
  ( cd "$dir" && find . -type f -printf '%P\n' | LC_ALL=C sort | xargs -r -d '\n' sha256sum ) \
    | LC_ALL=C sort -k2 | sha256sum | awk '{print $1}'
}

# 目录逐文件清单（sha256<空格><空格>相对路径），供人工核对与差异报告使用。
dir_file_list() {
  local dir="$1"
  ( cd "$dir" && find . -type f -printf '%P\n' | LC_ALL=C sort | xargs -r -d '\n' sha256sum ) | LC_ALL=C sort -k2
}

# ------------------------------------------------------------------ 环境检查 --

require_cmd() {
  local missing=()
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  [ ${#missing[@]} -eq 0 ] || die "缺少必需命令：${missing[*]}（请先安装后重试）"
}

check_docker() {
  command -v docker >/dev/null 2>&1 || die "未找到 docker 命令：Docker 未安装，或不在 PATH 中"
  docker info >/dev/null 2>&1 \
    || die "无法连接 Docker 守护进程：守护进程未运行，或当前用户无权限（需加入 docker 组，或以 root 执行）"
}

# Compose V2 门槛：离线编排用到 service_completed_successfully 条件，该条件只在 V2（≥2.0.0）存在，
# Python 实现的 docker-compose 1.x 永不支持。
check_compose_v2() {
  docker compose version >/dev/null 2>&1 \
    || die "未检测到 Docker Compose V2（docker compose version 不可用）。若本机只有 docker-compose（连字符）1.x，
        请回报 'docker-compose --version' 输出，开发侧改用 docker run 形态的安装脚本。"
  local ver
  ver="$(docker compose version --short 2>/dev/null || echo '0')"
  log "Docker Compose 版本：${ver}"
}

# 磁盘余量检查：目标路径所在分区可用空间不低于 need_gb。
check_disk() {
  local path="$1" need_gb="$2" avail_gb
  avail_gb="$(df -BG --output=avail "$path" 2>/dev/null | tail -1 | tr -dc '0-9')"
  [ -n "$avail_gb" ] || { warn "无法探测 ${path} 的可用空间，跳过磁盘检查"; return 0; }
  log "磁盘可用空间：${path} → ${avail_gb} GB（门槛 ${need_gb} GB）"
  [ "$avail_gb" -ge "$need_gb" ] || die "${path} 可用空间 ${avail_gb} GB 低于门槛 ${need_gb} GB"
}

# 端口占用检查：被占用即失败并打印占用者（能取到时）。
check_port_free() {
  local port="$1"
  local line=""
  if command -v ss >/dev/null 2>&1; then
    line="$(ss -ltnp 2>/dev/null | awk -v p=":${port}$" '$4 ~ p')"
  elif command -v netstat >/dev/null 2>&1; then
    line="$(netstat -ltnp 2>/dev/null | awk -v p=":${port}$" '$4 ~ p')"
  else
    warn "缺少 ss 与 netstat，跳过端口 ${port} 占用检查"
    return 0
  fi
  [ -z "$line" ] || die "端口 ${port} 已被占用：${line}"
  log "端口 ${port} 空闲"
}

# ------------------------------------------------------------------ manifest --
# 只用 python3 读 JSON（离线机可能没有 jq；python3 是本包镜像外唯一的宿主依赖，缺失时明确报错）。

manifest_get() {
  local file="$1" expr="$2"
  python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    d = d[int(k)] if isinstance(d, list) else d[k]
print(d if not isinstance(d,(dict,list)) else json.dumps(d,ensure_ascii=False))
' "$file" "$expr"
}

# 期望值/实测值比对：一致返回 0，不一致打印三列并返回 1（调用方决定是否致命）。
compare_value() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    printf '  [一致] %-28s %s\n' "$label" "$expected"
    return 0
  fi
  printf '  [不符] %-28s 期望=%s 实测=%s\n' "$label" "$expected" "$actual"
  return 1
}
