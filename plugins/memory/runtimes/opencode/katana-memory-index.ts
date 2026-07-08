// OpenCode plugin：把 katana memory 的 <memory-index> 注入 system prompt。
//
// 机制：experimental.chat.system.transform 每条消息触发；按 sessionID 缓存，
// 同一 session 内容 pin 住（prompt cache 友好），等价 Claude Code 的
// SessionStart 一次性注入。服务不可达时该 session 静默降级（与 curl hook 一致，
// agent 仍可通过 MCP tool memory_index 主动拉取）。
//
// 兼容性：命名导出 async 函数（legacy plugin 形态）、零 import，
// 新旧两代 plugin loader（getServerPlugin / readV1Plugin）均可加载。

const URL_BASE = process.env["KATANA_MEMORY_MCP_URL"] ?? "http://127.0.0.1:5605"
const TENANT = process.env["KATANA_MEMORY_TENANT"] ?? "uther"

export const KatanaMemoryIndexPlugin = async () => {
  const bySession = new Map<string, string>()

  async function fetchIndex(): Promise<string> {
    try {
      const res = await fetch(`${URL_BASE}/t/${TENANT}/index.md`, {
        signal: AbortSignal.timeout(3000),
      })
      if (!res.ok) return ""
      return await res.text()
    } catch {
      return ""
    }
  }

  return {
    "experimental.chat.system.transform": async (
      input: { sessionID?: string },
      output: { system: string[] },
    ) => {
      const key = input.sessionID
      let index = key ? bySession.get(key) : undefined
      if (index === undefined) {
        index = await fetchIndex()
        if (key) bySession.set(key, index)
      }
      if (index) output.system.push(index)
    },
  }
}
