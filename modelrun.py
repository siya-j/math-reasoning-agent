for m in \
  "openrouter:cohere/north-mini-code:free" \
  "openrouter:poolside/laguna-s-2.1:free" \
  "openrouter:poolside/laguna-xs-2.1:free" \
  "openrouter:inclusionai/ling-3.0-tiny:free"
do
  echo "=============================================="
  MRA_MODEL="$m" python scripts/probe_lean_model.py
done
