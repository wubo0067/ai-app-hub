
#!//bin/bash
# ==============================================================================
# Script Name: create_branch.sh (Professional Version)
# Description: Create, checkout and track a new branch on origin.
# Features    : Dirty-tree check, Remote existence check, Auto-naming,
#               Input validation, Strict mode.
# ==============================================================================

set -euo pipefail  # 增强型严格模式：遇到未定义变量报错，管道错误报错，遇错立即退出

# --- 配置区域 ---
DEFAULT_PREFIX="feature"
REMOTE_NAME="origin"

# --- 工具函数 ---
log_info()    { echo -e "\033[0;34m[INFO]\033[0m  $*" ; }
log_warn()    { echo -e "\033[0;33m[WARN]\033[0m] $*" ; }
log_error()   { echo -e "\033[0;31m[ERROR]\033[0m] $*" >&2 ; }

usage() {
    echo "Usage: $0 [branch_name]"
    echo "If no branch name is provided, a default '$DEFAULT_PREFIX/YYYY-MM-DD' will be used."
    exit 1
}

# --- 主逻辑 ---

# 1. 环境检查：验证 Git 仓库环境
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    log_error "Not a git repository (or any filial git directory)."
    exit 1
fi

# 2. 参数解析与校验
RAW_BRANCH_NAME="${1:-}"

if [ -z "$RAW_BRANCH_NAME" ]; then
    BRANCH_NAME="$DEFAULT_PREFIX/$(date +%Y-%m-%d_%H%M%S)"
    log_warn "No branch name provided. Auto-generated: $BRANCH_NAME"
else
    # 验证分支名是否包含非法字符 (不允许空格或特殊符号)
    if [[ "$RAW_BRANCH_NAME" =~ [[:space:]] ]] || [[ "$RAW_BRANCH_NAME" =~ [^a-zA-Z0-9/_.-] ]]; then
        log_error "Invalid branch name: '$RAW_BRANCH_NAME'. Use only alphanumeric, /, _, -, or ."
        exit 1
    fi
    BRANCH_NAME="$RAW_BRANCH_NAME"
fi

# 3. 工作区状态检查 (Critical!)
# 如果工作区有未提交的改动，强制停止以防止用户丢失代码或污染新分支。
if [[ -n $(git status --porcelain) ]]; then
    log_error "Working tree is dirty. Please commit or stash your changes before creating a new branch."
    exit 1
fi

# 4. 远程仓库检查
if ! git remote | grep -q "^$REMOTE_NAME$"; then
    log_error "Remote '$REMOTE_NAME' not found. Check your remotes with 'git remote'."
    exit 1
fi

# 5. 分支存在性逻辑 (处理本地和远程)
# 首先检查本地是否已存在
if git show-ref --verify --quiet "refs/heads/$BRANCH_NAME"; then
    log_warn "Local branch '$BRANCH_NAME' already exists."
elif git show-ref --verify --quiet "refs/remotes/$REMOTE_NAME/$BRANCH_NAME"; then
    # 如果本地没有，但远程有，则直接检出并跟踪远程分支
    log_info "Branch '$BRANCH_NAME' exists on $REMOTE_NAME. Tracking it..."
    git checkout -B "$BRANCH_NAME" --track "$REMOTE_NAME/$BRANCH_NAME"
    log_success "Switched to and tracked remote branch: $BRANCH_NAME"
    exit 0
else
    # 本地和远程都没有，进入创建流程
    log_info "Creating new local branch: $BRANCH_NAME"
    git checkout -b "$BRANCH_NAME"
fi

# 6. 推送到远端 (仅在是新分支时需要)
# 如果第5步直接 checkout 了已存在的本地/远程分支，这里就不执行 push。
if ! git rev-parse --verify "$BRANCH_NAME" > /dev/null 2>&1; then
    log_info "Pushing to $REMOTE_NAME..."
    git push -u "$REMOTE_NAME" "$BRANCH_NAME" || { log_error "Failed to push. Check your connection."; exit 1; }
    log_success "Branch '$BRANCH_NAME' is now tracking '$REMOTE_NAME/$BRANCH_NAME'."
fi

exit 0