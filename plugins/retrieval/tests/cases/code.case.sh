# code: (a) 本地 code root 可达且能读到已知文件; (b) clone 小公开 repo 到隔离 WORK_DIR 断言文件存在
CR_ENV="$(katana_config_get code_root_env "AGENT_CODE_ROOT" "")"
CR="$(eval echo "\${$CR_ENV:-}")"
if [ -z "$CR" ] || [ ! -d "$CR" ]; then
  skip "code:local" "code root unavailable"
else
  # 已知存在的 repo：katana 自身（self/katana）
  if [ -f "$CR/self/katana/README.md" ]; then pass "code:local"; else fail "code:local" "katana README not found under code root"; fi
fi
# clone case 必跑（隔离 WORK_DIR，跑完随 trap 清理；不写 code root，不污染本地）
if git clone --depth 1 https://github.com/octocat/Hello-World.git "$WORK_DIR/hello" >/dev/null 2>&1 && [ -f "$WORK_DIR/hello/README" ]; then
  pass "code:clone"
else
  fail "code:clone" "clone or file check failed"
fi
