#!/usr/bin/env bash
# デプロイスクリプト共通の Gemini モデル既定値。
# deploy.sh と deploy-nomig.sh はここを source し、モデル名を直書きしない。
# template.yaml の GeminiModel Default もこの値と揃える。

DEFAULT_GEMINI_MODEL="gemini-3.5-flash-lite"

apply_gemini_model_default() {
  if [[ -z "${DEFAULT_GEMINI_MODEL:-}" ]]; then
    echo "DEFAULT_GEMINI_MODEL is empty. Set it in scripts/gemini_model_default.sh." >&2
    exit 1
  fi
  # 未設定だけ既定値にする。空文字は上書きミスなので止める。
  if [[ ! -v GEMINI_MODEL ]]; then
    GEMINI_MODEL="$DEFAULT_GEMINI_MODEL"
    return 0
  fi
  local trimmed="${GEMINI_MODEL#"${GEMINI_MODEL%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  if [[ -z "$trimmed" ]]; then
    echo "GEMINI_MODEL is empty. Unset it to use ${DEFAULT_GEMINI_MODEL}, or set a model name." >&2
    exit 1
  fi
  GEMINI_MODEL="$trimmed"
}
