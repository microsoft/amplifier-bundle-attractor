#!/usr/bin/env bash
# install_provider_instances.sh -- make the pipelines' provider INSTANCES
# addressable on this runner, or REFUSE loudly before any budget is spent.
#
# WHY THIS EXISTS. The shipped pipelines' judgment nodes address a provider
# INSTANCE by id (`llm_provider="luna"`). An instance lives in the operator's
# merged Amplifier settings, and a GitHub-hosted runner has none -- so without
# this step the engine's issue-#155 startup preflight refuses the run, naming
# the node. That refusal is correct and stays exactly as it is; what this
# script does is give the runner a real instance to address, and fail EARLIER
# and more specifically when it cannot (naming the missing secret, before the
# multi-hour job even starts).
#
# It replaces the older per-workflow `OPENAI_API_KEY` preflight, which checked
# a credential the graphs no longer declare.
#
# REQUIRED vs OPTIONAL credentials (owner ruling, 2026-09-07). The template
# declares which of its `${VAR}` placeholders are OPTIONAL, in a machine-readable
# `optional-vars:` comment directive. A REQUIRED var that is missing is a
# refusal, exactly as before. An OPTIONAL var that is missing is not: its whole
# `key: ${VAR}` line is OMITTED from the installed file, so the provider module's
# own default for that field applies, and the step log SAYS SO. That is the
# difference between "the operator chose the default" and "the operator's
# endpoint silently went missing" -- one line of log is what makes the first
# readable as a choice.
#
# WHAT IT WILL NOT DO, deliberately:
#   - it never writes a secret to disk (the template's `${VAR}` placeholders go
#     down verbatim; the engine expands them from the environment at load time);
#   - it never overwrites settings the runner already has (a self-hosted runner
#     carrying the operator's own settings is the other supported shape, and
#     clobbering it would be the silent substitution this repo refuses);
#   - it never weakens the engine preflight to make a run start;
#   - it never omits a REQUIRED credential's line to make a run start, and it
#     refuses rather than guessing when an unset OPTIONAL var appears in a shape
#     it cannot surgically remove.
#
# Usage:  install_provider_instances.sh [<pipeline.dot> ...]
# Default targets: the three shipped pipelines in this directory.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/provider-instances.yaml"

# Provider MODULE names the engine resolves from its own closed table
# (loop-pipeline `provider_detection.PROVIDER_SPECS`). A node declaring one of
# these is NOT addressing an instance and needs nothing from this script.
PROVIDER_MODULES=" anthropic openai gemini github-copilot openai-chatgpt "

if [ "$#" -gt 0 ]; then
  DOT_FILES=("$@")
else
  DOT_FILES=(
    "$HERE/capsule.dot"
    "$HERE/feature-capsule.dot"
    "$HERE/task-runner.dot"
  )
fi

[ -f "$TEMPLATE" ] || {
  echo "::error::install_provider_instances: template not found at $TEMPLATE. This script and provider-instances.yaml ship together; a checkout carrying one without the other is broken." >&2
  exit 2
}

# --- 1. Which instance ids do the graphs actually address? -------------------
# Derived from the graphs, never hardcoded here: adding an instance pin to a
# .dot without defining it in provider-instances.yaml must fail HERE, not
# forty minutes into a run.
required_ids="$(
  grep -ho 'llm_provider="[^"]*"' "${DOT_FILES[@]}" 2>/dev/null \
    | sed 's/llm_provider="\(.*\)"/\1/' \
    | sort -u \
    | while read -r name; do
        case "$PROVIDER_MODULES" in
          *" $name "*) ;;            # a module name -- not an instance
          *) [ -n "$name" ] && echo "$name" ;;
        esac
      done
)"

if [ -z "$required_ids" ]; then
  echo "install_provider_instances: no graph addresses a provider instance; nothing to install."
  exit 0
fi

echo "install_provider_instances: graphs address these provider instances: $(echo "$required_ids" | tr '\n' ' ')"

# --- 2. Does the template define every one of them? --------------------------
missing_defs=""
for id in $required_ids; do
  grep -Eq "^[[:space:]]*-?[[:space:]]*id:[[:space:]]*${id}[[:space:]]*$" "$TEMPLATE" || missing_defs="$missing_defs $id"
done
if [ -n "$missing_defs" ]; then
  echo "::error::install_provider_instances: the pipelines address provider instance(s)$missing_defs, but $TEMPLATE defines no such id. A graph cannot name an instance this repo does not ship a runner-side definition for -- the run would refuse at the engine's startup preflight after the job had already been provisioned. Add the instance to provider-instances.yaml (with \${VAR} placeholders, never a literal credential) and name its secrets in the workflow." >&2
  exit 1
fi

# --- 3. Already addressable? Then leave the runner's own settings alone. -----
AMP_HOME="${AMPLIFIER_HOME:-$HOME/.amplifier}"
SETTINGS="$AMP_HOME/settings.yaml"

if [ -f "$SETTINGS" ]; then
  already_all=1
  for id in $required_ids; do
    grep -Eq "^[[:space:]]*-?[[:space:]]*id:[[:space:]]*${id}[[:space:]]*$" "$SETTINGS" || already_all=0
  done
  if [ "$already_all" -eq 1 ]; then
    echo "install_provider_instances: $SETTINGS already defines every instance the graphs address -- leaving it untouched (this is the self-hosted-runner shape)."
    exit 0
  fi
  echo "::error::install_provider_instances: $SETTINGS already exists but does not define every instance the graphs address ($(echo "$required_ids" | tr '\n' ' ')). Refusing to overwrite settings this runner already carries -- silently replacing an operator's provider configuration is exactly the substitution this pipeline refuses to make. Either add the instance(s) to that file, or run on a runner with no pre-existing Amplifier settings." >&2
  exit 1
fi

# --- 4. Are the credentials this template needs actually present? ------------
# Both lists are read OUT OF THE TEMPLATE, so neither can drift from what the
# instance definition actually references. Comments are stripped before the
# placeholder scan: the template's own header explains the `${VAR}` convention
# using a placeholder spelled `${VAR}`, and demanding an env var named VAR would
# be absurd. The `optional-vars:` directive is read from the comments on
# purpose -- it is metadata ABOUT the document, not part of it.
# shellcheck disable=SC2016  # the literal ${...} text is the search target
declared_vars="$(sed 's/#.*//' "$TEMPLATE" | grep -o '\${[A-Za-z_][A-Za-z0-9_]*}' | tr -d '${}' | sort -u)"
optional_vars="$(sed -n 's/^[[:space:]]*#[[:space:]]*optional-vars:[[:space:]]*//p' "$TEMPLATE" | tr ',' ' ' | tr -s ' \t' '\n' | sed '/^$/d' | sort -u)"

# An optional var the template never references is a typo in the directive, and
# a typo here silently re-hardens an optional credential into a required one.
for var in $optional_vars; do
  case " $(echo "$declared_vars" | tr '\n' ' ') " in
    *" $var "*) ;;
    *)
      echo "::error::install_provider_instances: $TEMPLATE declares '$var' in its optional-vars directive, but references no \${$var} placeholder. Fix the directive (or the placeholder) -- a name that matches nothing makes the optional/required split unreadable, and the next credential to go missing would be demanded or omitted by accident." >&2
      exit 2
      ;;
  esac
done

missing_vars=""
for var in $declared_vars; do
  case " $(echo "$optional_vars" | tr '\n' ' ') " in
    *" $var "*) continue ;;   # optional -- absence is a choice, not a fault
  esac
  # shellcheck disable=SC2154  # indirect expansion of a name read from the template
  if [ -z "${!var:-}" ]; then
    missing_vars="$missing_vars $var"
  fi
done
if [ -n "$missing_vars" ]; then
  echo "::error::install_provider_instances: missing credential(s) for the pipelines' judgment node:$missing_vars. The graphs declare llm_provider=\"$(echo "$required_ids" | tr '\n' ' ' | sed 's/ $//')\" -- a configured provider INSTANCE, and a second model family is the point: the judge must be independent of the maker's family (issue #155 records what happens when it silently is not). Provision each name above as a repo secret and pass it into this step's env. Refusing to start here, so the failure is visible before the run's multi-hour budget is spent -- and refusing rather than falling back, so no run ever reports a dual-family critique it did not have." >&2
  exit 1
fi

# --- 5. Install. Placeholders go down verbatim; no secret touches the disk. ---
# An optional var that is unset takes its whole `key: ${VAR}` line out of the
# installed copy, so the provider module's own default for that field applies.
# The deletion is deliberately narrow -- one plain mapping line whose value is
# exactly that placeholder -- and anything left over is a refusal rather than a
# guess: a `${VAR}` the engine cannot expand is left VERBATIM by design, which
# in a base_url would send the run at a literal "${OPENAI_BASE_URL}" host.
mkdir -p "$AMP_HOME"
staged="$(mktemp)"
trap 'rm -f "$staged"' EXIT
cp "$TEMPLATE" "$staged"

omitted_vars=""
present_vars=""
for var in $declared_vars; do
  if [ -n "${!var:-}" ]; then
    present_vars="$present_vars $var"
    continue
  fi
  # Unset, and (step 4 proved it) optional.
  line_re='^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*:[[:space:]]*\$\{'"$var"'\}[[:space:]]*$'
  sed -E "/$line_re/d" "$staged" > "$staged.next"
  mv "$staged.next" "$staged"
  if grep -qF "\${$var}" "$staged"; then
    echo "::error::install_provider_instances: \${$var} is unset and optional, but the template still references it after removing every plain \`key: \${$var}\` line -- it appears in a shape this installer cannot surgically omit (an inline value, a list entry, a quoted string). Refusing rather than installing a settings file carrying an unexpandable placeholder, which the engine leaves VERBATIM and the provider would then treat as a literal value. Put the placeholder on its own \`key: \${$var}\` line, or make the var required." >&2
    exit 2
  fi
  omitted_vars="$omitted_vars $var"
done

cp "$staged" "$SETTINGS"
chmod 600 "$SETTINGS"

echo "install_provider_instances: wrote $SETTINGS from $(basename "$TEMPLATE")."
echo "  instances installed : $(echo "$required_ids" | tr '\n' ' ')"
echo "  credentials present :$present_vars (values not printed; the file on disk holds \${VAR} placeholders, not secrets)"
if [ -n "$omitted_vars" ]; then
  echo "  OMITTED (unset, and declared optional):$omitted_vars"
  for var in $omitted_vars; do
    echo "    - $var is not set in this step's environment, so its key was left out of the installed instance and the provider module's default endpoint/value for that field applies. This is a supported configuration, not a degraded one: set the repository variable of that name to override it."
  done
else
  echo "  omitted             : (none -- every placeholder the template declares is set)"
fi
