#!/usr/bin/env bash
set -euo pipefail

# ステージング／本番共通の SAM デプロイスクリプト
# 1) 対象環境 DB へマイグレーション適用
# 2) SAM build / deploy
# 環境変数で上書き可能なパラメータ:
# STACK_NAME, PROFILE, REGION, STAGE, GEMINI_MODEL（既定は scripts/gemini_model_default.sh）, FUNCTION_MEMORY_SIZE, FUNCTION_TIMEOUT,
# MAX_CONTEXT_MESSAGES, TRANSLATION_RETRY, RUNTIME_SECRET_ARN, S3_BUCKET, PYTHON_BIN,
# TYPESAFE_API_KEY_SSM_PARAMETER（既定は STAGE に対応する既存名。stg→/kotori/stg/...、prod→/kotori/prod/...）

STACK_NAME="${STACK_NAME:-translate-line-bot-stg}"
PROFILE="${PROFILE:-line-translate-bot}"
REGION="${REGION:-ap-northeast-1}"
STAGE="${STAGE:-stg}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gemini_model_default.sh
source "${SCRIPT_DIR}/gemini_model_default.sh"
apply_gemini_model_default
FUNCTION_MEMORY_SIZE="${FUNCTION_MEMORY_SIZE:-512}"
FUNCTION_TIMEOUT="${FUNCTION_TIMEOUT:-60}"
MAX_CONTEXT_MESSAGES="${MAX_CONTEXT_MESSAGES:-8}"
MAX_GROUP_LANGUAGES="${MAX_GROUP_LANGUAGES:-5}"
TRANSLATION_RETRY="${TRANSLATION_RETRY:-2}"
RUNTIME_SECRET_ARN="${RUNTIME_SECRET_ARN:-}"
S3_BUCKET="${S3_BUCKET:-}"
ENABLE_STRIPE="${ENABLE_STRIPE:-true}"
DEPLOY_NONCE="${DEPLOY_NONCE:-$(date +%s)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -z "$RUNTIME_SECRET_ARN" ]]; then
  if [[ "$STAGE" == "prod" ]]; then
    RUNTIME_SECRET_ARN="prod/line-translate-bot-secrets"
  else
    RUNTIME_SECRET_ARN="stg/line-translate-bot-secrets"
  fi
fi

# Jev（TypeSafe）の API キーは SSM SecureString から読む。
# deploy.sh の STAGE は stg / prod（prd ではない）。既存パラメーター名にそのまま対応する。
# キーが無いとメンション判定が全部エラーになるので、存在しなければデプロイを止める。
# 値は出さない。--with-decryption は付けず、名前だけ確認する。
case "$STAGE" in
  stg)
    TYPESAFE_API_KEY_SSM_PARAMETER_DEFAULT="/kotori/stg/typesafe-api-key"
    ;;
  prod)
    TYPESAFE_API_KEY_SSM_PARAMETER_DEFAULT="/kotori/prod/typesafe-api-key"
    ;;
  *)
    echo "STAGE must be 'stg' or 'prod' (got '${STAGE}'). TypeSafe API key parameters are /kotori/stg/typesafe-api-key and /kotori/prod/typesafe-api-key." >&2
    exit 1
    ;;
esac
TYPESAFE_API_KEY_SSM_PARAMETER="${TYPESAFE_API_KEY_SSM_PARAMETER:-$TYPESAFE_API_KEY_SSM_PARAMETER_DEFAULT}"
# Lambda の実行ロールが読めるのは /kotori/<STAGE>/ 配下だけなので、それ以外は受け付けない
if [[ "$TYPESAFE_API_KEY_SSM_PARAMETER" != "/kotori/${STAGE}/"* ]]; then
  echo "TYPESAFE_API_KEY_SSM_PARAMETER must be under /kotori/${STAGE}/ (got '$TYPESAFE_API_KEY_SSM_PARAMETER')." >&2
  exit 1
fi
if ! aws ssm get-parameter \
  --name "$TYPESAFE_API_KEY_SSM_PARAMETER" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query Parameter.Name \
  --output text >/dev/null; then
  echo "SSM parameter '$TYPESAFE_API_KEY_SSM_PARAMETER' (TypeSafe API key) not found. Create it before deploying. The value was not printed." >&2
  exit 1
fi

if [[ -x ".venv/bin/python" && "$PYTHON_BIN" == "python3" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

echo "Applying DB migrations for stage '$STAGE' (secret=$RUNTIME_SECRET_ARN)..."
SECRET_JSON="$(aws secretsmanager get-secret-value \
  --secret-id "$RUNTIME_SECRET_ARN" \
  --region "$REGION" \
  --profile "$PROFILE" \
  --query SecretString \
  --output text)"
DB_URL="$(printf '%s' "$SECRET_JSON" | "$PYTHON_BIN" -c 'import sys, json; print(json.load(sys.stdin)["NEON_DATABASE_URL"])')"
"$PYTHON_BIN" scripts/apply_migrations.py --db-url "$DB_URL"

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
    MaxGroupLanguages="$MAX_GROUP_LANGUAGES"
    TranslationRetry="$TRANSLATION_RETRY"
    RuntimeSecretArn="$RUNTIME_SECRET_ARN"
    DeployNonce="$DEPLOY_NONCE"
    EnableStripe="$ENABLE_STRIPE"
    TypesafeApiKeySsmParameter="$TYPESAFE_API_KEY_SSM_PARAMETER"
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
