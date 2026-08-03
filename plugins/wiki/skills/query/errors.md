# Errors

## 2026-07-13 — JavaScript template literal 提前展开 shell 参数

- 症状：通过 `functions.exec` 组织 `awk`/shell 命令时，JavaScript template literal 把 shell 的 `${start}` 当作 JavaScript 插值，导致命令在执行前报错。
- 影响：命令未执行，没有读取或写入知识库。
- 规避：包含 shell `${...}` 的命令使用普通 JavaScript 字符串，或把 shell 变量改写为不需要花括号插值的形式；不要直接放进 JavaScript template literal。
