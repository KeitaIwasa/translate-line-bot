#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${LAMBDA_FUNCTION_NAME:-}" || -z "${AWS_REGION:-}" ]]; then
  echo "Please set LAMBDA_FUNCTION_NAME and AWS_REGION environment variables." >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$DIST_DIR/build"
PACKAGE_ZIP="$DIST_DIR/translate-line-bot.zip"

rm -rf "$DIST_DIR"
mkdir -p "$BUILD_DIR"

pip3 install --upgrade pip >/dev/null
pip3 install -r "$ROOT_DIR/requirements.txt" -t "$BUILD_DIR"

cp -R "$ROOT_DIR"/src/* "$BUILD_DIR"/

pushd "$BUILD_DIR" >/dev/null
zip -r "$PACKAGE_ZIP" .
popd >/dev/null

aws lambda update-function-code \
  --function-name "$LAMBDA_FUNCTION_NAME" \
  --region "$AWS_REGION" \
  --zip-file "fileb://$PACKAGE_ZIP"

echo "Deployed package: $PACKAGE_ZIP"
