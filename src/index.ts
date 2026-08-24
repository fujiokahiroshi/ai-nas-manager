import http from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const HTTP_PORT = process.env.MCP_HTTP_PORT ? Number(process.env.MCP_HTTP_PORT) : 39217;
// 0.0.0.0: WSL側からもWindows host経由(vEthernetアダプタ)でアクセスできるようにする。
// ループバック限定にしたい場合は MCP_HTTP_HOST=127.0.0.1 を設定する。
const HTTP_HOST = process.env.MCP_HTTP_HOST ?? "0.0.0.0";
const MAX_QUEUE_SIZE = 1000;
const LEADER_BASE_URL = `http://127.0.0.1:${HTTP_PORT}`;

interface IncomingMessage {
  id: number;
  text: string;
  source?: string;
  receivedAt: string;
}

interface OutgoingMessage {
  id: number;
  text: string;
  target?: string;
  sentAt: string;
}

// Linux(WSL)側 -> Claude 方向のキュー(POST /message で受信)
const messageQueue: IncomingMessage[] = [];
let nextInId = 1;

// Claude -> Linux(WSL)側 方向のキュー(GET /outbox でLinux側がポーリング取得)
const outboxQueue: OutgoingMessage[] = [];
let nextOutId = 1;

// Claude Desktop / Claude Code / 複数ウィンドウなど、同じMCPサーバー定義から
// 複数のプロセスが同時に起動されることがある。HTTPポートを取得できたプロセスだけが
// 「リーダー」としてキューの実体を持ち、ポートを取れなかった「フォロワー」は
// 自分のツール呼び出しをリーダーへHTTP経由で転送する。これによりどのプロセス経由で
// ツールが呼ばれても、キューが分裂せず一箇所に集約される。
let isLeader = false;

function readJsonBody(req: http.IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1_000_000) {
        req.destroy();
        reject(new Error("body too large"));
      }
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(body || "{}"));
      } catch {
        reject(new Error("invalid JSON body"));
      }
    });
  });
}

// Windows11/WSL側のクライアント(例: python/send_message.py)から
// POST http://<host>:39217/message でメッセージを受け取り、Claudeがget_messagesで読む。
// Claude側は send_to_linux ツールで outbox にメッセージを積み、
// Linux(WSL)側は GET http://<host>:39217/outbox をポーリングして受け取る(例: python/receive_message.py)。
// /internal/* はフォロワープロセスからリーダーへの内部転送専用。
const httpServer = http.createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/message") {
    try {
      const parsed = (await readJsonBody(req)) as { text?: unknown; source?: unknown };
      if (typeof parsed.text !== "string" || parsed.text.length === 0) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "text (non-empty string) is required" }));
        return;
      }

      const message: IncomingMessage = {
        id: nextInId++,
        text: parsed.text,
        source: typeof parsed.source === "string" ? parsed.source : undefined,
        receivedAt: new Date().toISOString(),
      };

      messageQueue.push(message);
      if (messageQueue.length > MAX_QUEUE_SIZE) {
        messageQueue.shift();
      }

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, id: message.id }));
    } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "invalid JSON body" }));
    }
    return;
  }

  if (req.method === "GET" && req.url === "/outbox") {
    const picked = outboxQueue.splice(0, outboxQueue.length);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ messages: picked }));
    return;
  }

  if (req.method === "POST" && req.url === "/internal/outbox/add") {
    try {
      const parsed = (await readJsonBody(req)) as { text?: unknown; target?: unknown };
      const message: OutgoingMessage = {
        id: nextOutId++,
        text: String(parsed.text ?? ""),
        target: typeof parsed.target === "string" ? parsed.target : undefined,
        sentAt: new Date().toISOString(),
      };
      outboxQueue.push(message);
      if (outboxQueue.length > MAX_QUEUE_SIZE) {
        outboxQueue.shift();
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ message }));
    } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "invalid JSON body" }));
    }
    return;
  }

  if (req.method === "POST" && req.url === "/internal/inbox/pop") {
    try {
      const parsed = (await readJsonBody(req)) as { limit?: unknown };
      const limit = typeof parsed.limit === "number" ? parsed.limit : messageQueue.length;
      const picked = messageQueue.splice(0, limit);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ messages: picked }));
    } catch {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "invalid JSON body" }));
    }
    return;
  }

  if (req.method === "GET" && req.url === "/internal/inbox/peek") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ messages: messageQueue }));
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "not found" }));
});

httpServer.on("error", (err: NodeJS.ErrnoException) => {
  if (err.code === "EADDRINUSE") {
    isLeader = false;
    console.error(
      `[windows-message-mcp] port ${HTTP_PORT} already in use by another instance; running as a follower (proxying to ${LEADER_BASE_URL}).`
    );
  } else {
    console.error(`[windows-message-mcp] HTTP server error: ${err.message}`);
  }
});

httpServer.listen(HTTP_PORT, HTTP_HOST, () => {
  isLeader = true;
  console.error(`[windows-message-mcp] listening for POST http://${HTTP_HOST}:${HTTP_PORT}/message (leader)`);
});

async function leaderRequest(path: string, init?: RequestInit): Promise<any> {
  const response = await fetch(`${LEADER_BASE_URL}${path}`, init);
  if (!response.ok) {
    throw new Error(`leader request failed: ${response.status}`);
  }
  return response.json();
}

const server = new McpServer({
  name: "windows-message-mcp",
  version: "1.0.0",
});

server.tool(
  "get_messages",
  "Windows11側から届いた未読メッセージを取得します。取得したメッセージはキューから削除されます(既読化)。",
  {
    limit: z
      .number()
      .int()
      .positive()
      .max(100)
      .optional()
      .describe("取得する最大件数。省略時は溜まっている全件を取得します。"),
  },
  async ({ limit }) => {
    let picked: IncomingMessage[];
    if (isLeader) {
      const take = limit ?? messageQueue.length;
      picked = messageQueue.splice(0, take);
    } else {
      const result = await leaderRequest("/internal/inbox/pop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit }),
      });
      picked = result.messages;
    }

    return {
      content: [
        {
          type: "text",
          text:
            picked.length === 0
              ? "未読メッセージはありません。"
              : JSON.stringify(picked, null, 2),
        },
      ],
    };
  }
);

server.tool(
  "send_to_linux",
  "このマシン上の単一の共有outboxキューにメッセージを1件積む(即時・確認不要)。宛先の選択や解決は一切不要: outboxは1つしかなく、Linux(WSL)側でpython/receive_message.pyのようなポーリングスクリプトを動かしている人が次回ポーリング時に受け取る。ユーザーから「WSLに送って」「Linuxに通知して」のように頼まれたら、textにそのメッセージ内容をそのまま入れて即座にこのツールを呼ぶこと。送信先を尋ねたり確認したりする必要はない。",
  {
    text: z.string().min(1).describe("送信するメッセージ本文。ユーザーが送ってほしいと言った内容をそのまま入れる。"),
    target: z
      .string()
      .optional()
      .describe(
        "任意の自由記述ラベル(outboxを複数の受信スクリプトで分けて使いたい場合の目印)。ユーザーが明示的に指定しない限り省略してよく、確認する必要はない。"
      ),
  },
  async ({ text, target }) => {
    let id: number;
    if (isLeader) {
      const message: OutgoingMessage = {
        id: nextOutId++,
        text,
        target,
        sentAt: new Date().toISOString(),
      };
      outboxQueue.push(message);
      if (outboxQueue.length > MAX_QUEUE_SIZE) {
        outboxQueue.shift();
      }
      id = message.id;
    } else {
      const result = await leaderRequest("/internal/outbox/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, target }),
      });
      id = result.message.id;
    }

    return {
      content: [
        {
          type: "text",
          text: `outboxに追加しました(id=${id})。Linux側のポーリングスクリプトが次回チェック時に取得します。`,
        },
      ],
    };
  }
);

server.tool(
  "peek_messages",
  "キューを消費せずに、現在溜まっているメッセージの件数と内容を確認します。",
  {},
  async () => {
    const messages = isLeader ? messageQueue : (await leaderRequest("/internal/inbox/peek")).messages;
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify({ count: messages.length, messages }, null, 2),
        },
      ],
    };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
