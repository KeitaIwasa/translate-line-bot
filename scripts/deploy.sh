#!/usr/bin/env bash
set -euo pipefail

# ステージング／本番共通の SAM デプロイスクリプト
# 環境変数で上書き可能なパラメータ:
# STACK_NAME, PROFILE, REGION, STAGE, GEMINI_MODEL, FUNCTION_MEMORY_SIZE, FUNCTION_TIMEOUT,
# MAX_CONTEXT_MESSAGES, TRANSLATION_RETRY, RUNTIME_SECRET_ARN, S3_BUCKET

STACK_NAME="${STACK_NAME:-translate-line-bot-stg}"
PROFILE="${PROFILE:-line-translate-bot}"
REGION="${REGION:-ap-northeast-1}"
STAGE="${STAGE:-stg}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
FUNCTION_MEMORY_SIZE="${FUNCTION_MEMORY_SIZE:-512}"
FUNCTION_TIMEOUT="${FUNCTION_TIMEOUT:-20}"
MAX_CONTEXT_MESSAGES="${MAX_CONTEXT_MESSAGES:-8}"
TRANSLATION_RETRY="${TRANSLATION_RETRY:-2}"
RUNTIME_SECRET_ARN="${RUNTIME_SECRET_ARN:-}"
S3_BUCKET="${S3_BUCKET:-}"

if [[ -z "$RUNTIME_SECRET_ARN" ]]; then
  if [[ "$STAGE" == "prod" ]]; then
    RUNTIME_SECRET_ARN="prod/line-translate-bot-secrets"
  else
    RUNTIME_SECRET_ARN="stg/line-translate-bot-secrets"
  fi
fi

echo "SAM Build..."
sam build

deploy_args=(
  --stack-name "$STACK_NAME"
  --region "$REGION"
  --profile "$PROFILE"
  --capabilities CAPABILITY_IAM
  --parameter-overrides
    StageName="$STAGE"
    FunctionMemorySize="$FUNCTION_MEMORY_SIZE"
    FunctionTimeout="$FUNCTION_TIMEOUT"
    GeminiModel="$GEMINI_MODEL"
    MaxContextMessages="$MAX_CONTEXT_MESSAGES"
    TranslationRetry="$TRANSLATION_RETRY"
    RuntimeSecretArn="$RUNTIME_SECRET_ARN"
)

if [[ -n "$S3_BUCKET" ]]; then
  echo "Using provided S3 bucket: $S3_BUCKET"
  deploy_args+=(--s3-bucket "$S3_BUCKET")
else
  echo "No S3 bucket provided. Using --resolve-s3 to create/use a managed bucket."
  deploy_args+=(--resolve-s3)
fi

echo "Deploying stack '$STACK_NAME' to region '$REGION' (stage=$STAGE, profile=$PROFILE)..."
sam deploy "${deploy_args[@]}"

echo "Deploy completed."
