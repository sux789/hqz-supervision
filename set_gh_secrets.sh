#!/usr/bin/env bash
# 一键配置 GitHub Actions 签名 Secrets（先 gh auth login，再跑本脚本）
# 用法：bash set_gh_secrets.sh
set -euo pipefail
cd "$(dirname "$0")"
REPO=sux789/hqz-supervision
P=android/keystore.properties
[ -f "$P" ] || { echo "缺少 $P（签名凭据）"; exit 1; }
[ -f android/supervision-release.jks ] || { echo "缺少 android/supervision-release.jks"; exit 1; }

ALIAS=$(awk '/^keyAlias:/{print $2}' "$P")
SP=$(awk '/^storePassword:/{print $2}' "$P")
KP=$(awk '/^keyPassword:/{print $2}' "$P")

base64 -i android/supervision-release.jks | gh secret set KEYSTORE_BASE64 -R "$REPO"
gh secret set KEY_ALIAS         -R "$REPO" --body "$ALIAS"
gh secret set KEYSTORE_PASSWORD -R "$REPO" --body "$SP"
gh secret set KEY_PASSWORD      -R "$REPO" --body "$KP"
echo "=== 已配置 ==="
gh secret list -R "$REPO"
