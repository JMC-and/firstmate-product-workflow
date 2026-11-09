#!/usr/bin/env bash
# Shared lifecycle adapter; the executable validator owns all predicates.
# fm_product_contract_gate <home> <task> [build|ready] [required|legacy] [head] [expected-digest] [launch-view]
# Legacy means only an already recorded unmarked task, never a new dispatch.
# No source text or contract field is evaluated as a command.
fm_product_contract_gate() {
  local home=$1 task=$2 phase=${3:-build} policy=${4:-legacy} head=${5:-} expected=${6:-} view=${7:-} root
  root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || return 1
  set -- check-task "$task" --phase "$phase"
  [ "$policy" != required ] || set -- "$@" --required
  [ -z "$head" ] || set -- "$@" --head "$head"
  [ -z "$expected" ] || set -- "$@" --expected-digest "$expected"
  [ "$view" != launch-view ] || set -- "$@" --launch-view
  FM_HOME="$home" bash "$root/fm-product-contract.sh" "$@"
}

# Dedicated build admission before endpoint/worktree allocation. The executable
# owns roster/provenance policy; ordinary homes retain their existing checks.
# fm_product_build_guard <home> <task> <kind> <project> <mode> <yolo>
fm_product_build_guard() {
  local home=$1 task=$2 kind=$3 project=$4 mode=$5 yolo=$6 root
  root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || return 1
  if [ ! -e "$home/.fm-product-build.json" ] && [ ! -L "$home/.fm-product-build.json" ]; then
    case "$kind" in secondmate|ship|scout) ;; *) return 0 ;; esac
    [ -e "${FM_DATA_OVERRIDE:-$home/data}/product-build/owners.json" ] \
      || [ -L "${FM_DATA_OVERRIDE:-$home/data}/product-build/owners.json" ] \
      || [ -e "$project/.fm-product-build.json" ] || [ -L "$project/.fm-product-build.json" ] || return 0
  fi
  python3 -c 'import json,sys; print(json.dumps(dict(zip(("kind","project","mode","yolo"),sys.argv[1:]))))' \
    "$kind" "$project" "$mode" "$yolo" |
    FM_HOME="$home" python3 "$root/fm-product-build.py" guard "$task"
}

# Render a trusted shell prefix that stops the entire remaining compound launch
# on a refusal. Test this emitted interface by executing it, not matching bytes.
# fm_product_contract_launch_guard <home> <task> <root> <policy> <state> <data> [expected-digest]
fm_product_contract_launch_guard() {
  local home=$1 task=$2 root=$3 policy=$4 state=$5 data=$6 expected=${7:-} required='' expected_arg=''
  [ "$policy" != required ] || required=' --required'
  [ -z "$expected" ] || printf -v expected_arg ' --expected-digest %q' "$expected"
  printf 'FM_HOME=%q FM_STATE_OVERRIDE=%q FM_DATA_OVERRIDE=%q bash %q check-task %q%s%s --launch-view >/dev/null || { return 1 2>/dev/null || exit 1; }; ' \
    "$home" "$state" "$data" "$root/bin/fm-product-contract.sh" "$task" "$required" "$expected_arg"
}

# One generated worker instruction for ship scaffolds and scout promotion.
# fm_product_contract_worker_block <home> <task> <code-root> [state] [data]
fm_product_contract_worker_block() {
  local home=$1 task=$2 root=$3 state=${4:-$1/state} data=${5:-$1/data} home_q task_q command_q state_q data_q
  printf -v home_q '%q' "$home"
  printf -v task_q '%q' "$task"
  printf -v command_q '%q' "$root/bin/fm-product-contract.sh"
  printf -v state_q '%q' "$state"
  printf -v data_q '%q' "$data"
  cat <<EOF
# Product contract
Product contract: required
Before product edits, dependency installation, scaffolding, and after interruption or changed requirements, run \`FM_HOME=$home_q FM_STATE_OVERRIDE=$state_q FM_DATA_OVERRIDE=$data_q bash $command_q check-task $task_q --required\`.
Read your assigned requirements and acceptance instructions with \`FM_HOME=$home_q FM_STATE_OVERRIDE=$state_q FM_DATA_OVERRIDE=$data_q bash $command_q instructions $task_q\`.
A refusal means stop product work and report the exact missing IDs to firstmate, not weaken scope or repair approval yourself.
The approved contract is the product authority; stop and report any mismatch with task instructions rather than interpreting it as changed scope.
The assigned contract is read-only to you; write acceptance observations under your task's proof directory for firstmate to record through the shared evaluator.
No implementation result, passing test count, synthetic fixture, or disabled path is a product-completion claim.
The selected delivery path still owns implementation and validation; these acceptance obligations do not create a separate review pipeline or grant external-operation, merge, or publication authority.
EOF
}
