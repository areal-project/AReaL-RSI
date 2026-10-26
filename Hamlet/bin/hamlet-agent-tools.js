#!/usr/bin/env node
import { runAgentTools } from "../packages/agent-tools/dist/index.js";
try {
  await runAgentTools();
} catch {
  console.error("Hamlet Agent 工具启动失败；请检查实例连接环境。");
  process.exitCode = 1;
}
