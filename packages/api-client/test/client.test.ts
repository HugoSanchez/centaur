import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CentaurClient,
  buildSessionInputLine,
  type StreamEvent,
} from "../src/client";
import {
  DEFAULT_HARNESS_MODEL_ID,
  HARNESS_MODEL_GROUPS,
  harnessModelPayload,
} from "../src/model-catalog";

async function collectEvents(events: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
  const collected: StreamEvent[] = [];
  for await (const event of events) {
    collected.push(event);
  }
  return collected;
}

function sseResponse(body: string, init?: ResponseInit): Response {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
      ...init,
    },
  );
}

describe("CentaurClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("parses SSE ids, events, JSON data, [DONE], and invalid JSON payloads", async () => {
    const fetchMock = vi.fn(async () => sseResponse([
      "id: 11",
      "event: amp_raw_event",
      'data: {"type":"assistant","message":{"content":"hello"}}',
      "",
      "id: 12",
      "event: done",
      "data: [DONE]",
      "",
      "id: 13",
      "data: not-json",
      "",
      "",
    ].join("\n")));
    vi.stubGlobal("fetch", fetchMock);

    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });

    await expect(collectEvents(client.streamEvents({ threadKey: "thread-1" }))).resolves.toEqual([
      {
        eventId: 11,
        eventKind: "amp_raw_event",
        data: { type: "assistant", message: { content: "hello" } },
      },
      {
        eventId: 13,
        eventKind: "message",
        data: { type: "unknown", raw: "not-json" },
      },
    ]);
  });

  it("URL encodes Slack thread keys in event stream URLs", async () => {
    const fetchMock = vi.fn(async () => sseResponse(""));
    vi.stubGlobal("fetch", fetchMock);
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });

    await collectEvents(client.streamEvents({
      threadKey: "slack:C123:1700000000.000100",
      executionId: "exe-1",
      afterEventId: 42,
      pollMs: 250,
    }));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.local/agent/threads/slack%3AC123%3A1700000000.000100/events?after_event_id=42&execution_id=exe-1&poll_ms=250",
      expect.objectContaining({
        method: "GET",
        headers: {
          Authorization: "Bearer test-key",
          "X-Centaur-Thread-Key": "slack:C123:1700000000.000100",
        },
      }),
    );
  });

  it("URL encodes Slack thread keys in path-based API calls", async () => {
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });
    const getMock = vi.spyOn(client.http, "get").mockResolvedValue({
      data: { thread_key: "slack:C123:1700000000.000100", executions: [] },
    });
    const postMock = vi.spyOn(client.http, "post").mockResolvedValue({ data: { ok: true } });

    await client.listExecutions("slack:C123:1700000000.000100", 2);
    await client.releaseThread("slack:C123:1700000000.000100", {
      releaseId: "release:1",
      cancelInflight: true,
    });

    expect(getMock).toHaveBeenCalledWith(
      "/agent/threads/slack%3AC123%3A1700000000.000100/executions",
      { params: { limit: 2 } },
    );
    expect(postMock).toHaveBeenCalledWith(
      "/agent/threads/slack%3AC123%3A1700000000.000100/release",
      {
        release_id: "release:1",
        cancel_inflight: true,
      },
    );
  });

  it("posts the expected session turn payload for selected harness and model", async () => {
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });
    const postMock = vi.spyOn(client.http, "post")
      .mockResolvedValueOnce({
        data: {
          thread_key: "slack:C123:1700000000.000100",
          harness_type: "claudecode",
          harness_switched: true,
          status: "active",
        },
      })
      .mockResolvedValueOnce({ data: { ok: true, message_ids: ["msg-1"] } })
      .mockResolvedValueOnce({
        data: {
          ok: true,
          execution_id: "exe-1",
          thread_key: "slack:C123:1700000000.000100",
          status: "running",
        },
      });

    await expect(client.sendSessionTurn({
      threadKey: "slack:C123:1700000000.000100",
      text: "ship it",
      harnessType: "claudecode",
      model: "claude-sonnet-4-6",
      messageId: "client-msg-1",
      restartOnHarnessConflict: true,
      metadata: { source: "verso" },
      idleTimeoutMs: 60_000,
      maxDurationMs: 180_000,
    })).resolves.toMatchObject({
      messageIds: ["msg-1"],
      execution: { execution_id: "exe-1" },
      session: { harness_switched: true },
    });

    expect(postMock).toHaveBeenNthCalledWith(
      1,
      "/api/session/slack%3AC123%3A1700000000.000100",
      {
        harness_type: "claudecode",
        persona_id: null,
        metadata: { source: "verso" },
        on_harness_conflict: "restart",
      },
    );
    expect(postMock).toHaveBeenNthCalledWith(
      2,
      "/api/session/slack%3AC123%3A1700000000.000100/messages",
      {
        messages: [{
          client_message_id: "client-msg-1",
          role: "user",
          parts: [{ type: "text", text: "ship it" }],
          metadata: { source: "verso" },
        }],
      },
    );

    const executeBody = postMock.mock.calls[2]?.[1] as {
      input_lines: string[];
      idempotency_key: string;
      idle_timeout_ms: number;
      max_duration_ms: number;
    };
    expect(postMock.mock.calls[2]?.[0]).toBe(
      "/api/session/slack%3AC123%3A1700000000.000100/execute",
    );
    expect(executeBody.idempotency_key).toBe("client-msg-1");
    expect(executeBody.idle_timeout_ms).toBe(60_000);
    expect(executeBody.max_duration_ms).toBe(180_000);
    expect(JSON.parse(executeBody.input_lines[0]!)).toMatchObject({
      type: "user",
      thread_key: "slack:C123:1700000000.000100",
      model: "claude-sonnet-4-6",
      trace_metadata: { source: "verso" },
      client_user_message_id: "client-msg-1",
      message: {
        role: "user",
        content: [{ type: "text", text: "ship it" }],
      },
    });
  });

  it("builds codex input lines with provider and reasoning overrides", () => {
    expect(JSON.parse(buildSessionInputLine({
      threadKey: "chat:1",
      text: "hello",
      model: "gpt-5.5",
      provider: "openai",
      reasoning: "high",
    }))).toEqual({
      type: "user",
      thread_key: "chat:1",
      model: "gpt-5.5",
      provider: "openai",
      reasoning: "high",
      message: {
        role: "user",
        content: [{ type: "text", text: "hello" }],
      },
    });
  });

  it("streams session API events from the deployed POC route shape", async () => {
    const fetchMock = vi.fn(async () => sseResponse([
      "id: 1",
      "event: session.output.line",
      'data: {"msg":"hi"}',
      "",
      "id: 2",
      "event: session.execution_completed",
      'data: {"result_text":"done"}',
      "",
      "",
    ].join("\n")));
    vi.stubGlobal("fetch", fetchMock);

    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });

    await expect(collectEvents(client.streamSessionEvents({
      threadKey: "slack:C123:1700000000.000100",
      executionId: "exe-1",
      afterEventId: 0,
    }))).resolves.toEqual([
      {
        eventId: 1,
        eventKind: "session.output.line",
        data: { msg: "hi" },
      },
      {
        eventId: 2,
        eventKind: "session.execution_completed",
        data: { result_text: "done" },
      },
    ]);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.local/api/session/slack%3AC123%3A1700000000.000100/events?after_event_id=0&execution_id=exe-1",
      expect.objectContaining({
        method: "GET",
        headers: { Authorization: "Bearer test-key" },
      }),
    );
  });

  it("exposes selector catalog payloads for the default model", () => {
    expect(HARNESS_MODEL_GROUPS.map((group) => group.id)).toEqual([
      "claudecode",
      "codex",
      "amp",
    ]);
    expect(harnessModelPayload(DEFAULT_HARNESS_MODEL_ID)).toEqual({
      harnessType: "codex",
      model: "gpt-5.5",
      provider: "openai",
    });
  });

  it("throws useful errors for non-OK event stream responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      "upstream unavailable",
      { status: 503, statusText: "Service Unavailable" },
    )));
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });

    await expect(
      collectEvents(client.streamEvents({ threadKey: "slack:C123:1700000000.000100" })),
    ).rejects.toThrow(
      "/agent/threads/{thread}/events failed (503): upstream unavailable",
    );
  });

  it("posts the expected steerExecution payload", async () => {
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });
    const postMock = vi.spyOn(client.http, "post").mockResolvedValue({ data: { ok: true } });

    await client.steerExecution("exe:123", {
      contentBlocks: [{ type: "text", text: "replacement" }],
      messageId: "slack:1700000000.000200",
      userId: "U123",
      metadata: { platform: "slack" },
      suppressCancellationDelivery: true,
    });

    expect(postMock).toHaveBeenCalledWith(
      "/agent/executions/exe%3A123/steer",
      {
        content_blocks: [{ type: "text", text: "replacement" }],
        message_id: "slack:1700000000.000200",
        user_id: "U123",
        metadata: {
          platform: "slack",
          steer_replacement: true,
        },
      },
    );
  });

  it("posts the expected final-delivery payloads", async () => {
    const client = new CentaurClient({
      apiUrl: "http://api.local",
      apiKey: "test-key",
    });
    const postMock = vi.spyOn(client.http, "post").mockResolvedValue({ data: { ok: true, deliveries: [] } });

    await client.claimFinalDeliveries({
      consumerId: "slackbot-1",
      limit: 3,
      leaseSeconds: 120,
      platform: "slack",
    });
    await client.renewFinalDeliveryLease("exe:123", {
      consumerId: "slackbot-1",
      leaseSeconds: 90,
    });
    await client.markFinalDelivered("exe:123", "slackbot-1");
    await client.markFinalFailed("exe:123", "rate limited", {
      consumerId: "slackbot-1",
      retryAfterSeconds: 45,
      nonRetryable: true,
      errorClass: "slack_rate_limit",
    });

    expect(postMock).toHaveBeenNthCalledWith(
      1,
      "/agent/final-deliveries/claim",
      {
        consumer_id: "slackbot-1",
        limit: 3,
        lease_seconds: 120,
        platform: "slack",
      },
    );
    expect(postMock).toHaveBeenNthCalledWith(
      2,
      "/agent/final-deliveries/exe%3A123/heartbeat",
      {
        consumer_id: "slackbot-1",
        lease_seconds: 90,
      },
    );
    expect(postMock).toHaveBeenNthCalledWith(
      3,
      "/agent/final-deliveries/exe%3A123/delivered",
      { consumer_id: "slackbot-1" },
    );
    expect(postMock).toHaveBeenNthCalledWith(
      4,
      "/agent/final-deliveries/exe%3A123/failed",
      {
        consumer_id: "slackbot-1",
        error: "rate limited",
        retry_after_seconds: 45,
        non_retryable: true,
        error_class: "slack_rate_limit",
      },
    );
  });
});
