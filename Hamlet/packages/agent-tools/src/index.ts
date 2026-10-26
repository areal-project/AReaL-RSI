import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { messageTools } from "./definitions.js";

export function createAgentToolServer(env: NodeJS.ProcessEnv = process.env) {
  const {
    HAMLET_URL: url,
    HAMLET_TOKEN: token,
    HAMLET_INSTANCE_ID: instanceId,
  } = env;
  if (!url || !token || !instanceId)
    throw new Error("Hamlet 实例工具连接信息不完整");
  const endpoint = new URL(url);
  if (
    !["http:", "https:"].includes(endpoint.protocol) ||
    endpoint.username ||
    endpoint.password
  )
    throw new Error("Hamlet 实例工具服务地址无效");
  const server = new Server(
    { name: "hamlet", version: "1.0.0" },
    { capabilities: { tools: {} } },
  );
  const request = async (path: string, body?: unknown) => {
    const response = await fetch(new URL(path, endpoint), {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(30000),
    });
    const value = (await response.json()) as any;
    if (!response.ok)
      throw Object.assign(new Error(value.error || "Hamlet 工具请求失败"), {
        statusCode: response.status,
        details: value.details,
      });
    return value;
  };
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: messageTools,
  }));
  server.setRequestHandler(CallToolRequestSchema, async ({ params }) => {
    try {
      const definition = messageTools.find((tool) => tool.name === params.name);
      if (!definition) throw new Error("未知 Hamlet 工具");
      const args = params.arguments || {};
      const properties = definition.inputSchema.properties as Record<
        string,
        { type: string; minLength?: number } | undefined
      >;
      for (const key of definition.inputSchema.required)
        if (!Object.hasOwn(args, key)) throw new Error(`${key} 必填`);
      for (const [key, value] of Object.entries(args)) {
        const field = properties[key];
        if (
          !field ||
          typeof value !== field.type ||
          value === null ||
          Array.isArray(value) ||
          (field.minLength && String(value).length < field.minLength)
        )
          throw new Error(`工具参数 ${key} 无效`);
      }
      // Verify the live actor before every mutation; never discover Owner credentials.
      const actor = await request("/api/identity");
      if (
        actor.id !== instanceId ||
        !["admin", "worker"].includes(actor.identity)
      )
        throw new Error("Hamlet 工具仅允许当前 Agent 实例身份");
      const result = await request(`/api/tools/${params.name}`, args);
      return {
        content: [{ type: "text" as const, text: JSON.stringify(result) }],
        structuredContent: result,
      };
    } catch (error: any) {
      const result = {
        error: error.statusCode
          ? error.message
          : error instanceof TypeError
            ? "Hamlet 服务连接失败"
            : error.message,
        ...(error.statusCode ? { statusCode: error.statusCode } : {}),
        ...(error.details ? { details: error.details } : {}),
      };
      return {
        isError: true,
        content: [{ type: "text" as const, text: JSON.stringify(result) }],
      };
    }
  });
  return server;
}

export async function runAgentTools() {
  const server = createAgentToolServer();
  await server.connect(new StdioServerTransport());
}
