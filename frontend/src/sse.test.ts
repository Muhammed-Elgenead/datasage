import { describe, expect, it } from "vitest";

import { SSEParser } from "./sse";

describe("SSEParser", () => {
  it("parses complete frames", () => {
    const p = new SSEParser();
    expect(p.feed('event: a\ndata: {"x":1}\n\nevent: b\ndata: 2\n\n')).toEqual([
      { event: "a", data: '{"x":1}' },
      { event: "b", data: "2" },
    ]);
  });

  it("buffers frames split across chunks at any point", () => {
    const text = 'event: text_delta\ndata: {"t":"hello"}\n\nevent: done\ndata: {}\n\n';
    for (let cut = 1; cut < text.length; cut++) {
      const p = new SSEParser();
      const frames = [...p.feed(text.slice(0, cut)), ...p.feed(text.slice(cut))];
      expect(frames.map((f) => f.event)).toEqual(["text_delta", "done"]);
    }
  });

  it("ignores keep-alive comments and handles CRLF", () => {
    const p = new SSEParser();
    expect(p.feed(": keep-alive\r\n\r\nevent: x\r\ndata: 1\r\n\r\n")).toEqual([{ event: "x", data: "1" }]);
  });

  it("joins multi-line data and defaults the event name", () => {
    const p = new SSEParser();
    expect(p.feed("data: a\ndata: b\n\n")).toEqual([{ event: "message", data: "a\nb" }]);
  });

  it("flushes a final frame without its blank line", () => {
    const p = new SSEParser();
    expect(p.feed("event: last\ndata: 1")).toEqual([]);
    expect(p.flush()).toEqual([{ event: "last", data: "1" }]);
    expect(p.flush()).toEqual([]);
  });
});
