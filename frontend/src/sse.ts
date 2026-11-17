// Incremental Server-Sent Events parser. Network chunks can split a frame anywhere
// (even inside a multi-byte character, handled by TextDecoder upstream), so text is
// buffered until a blank line ends the frame.

export interface SSEFrame {
  event: string;
  data: string;
}

export class SSEParser {
  private buffer = "";

  feed(chunk: string): SSEFrame[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const frames: SSEFrame[] = [];
    let end = this.buffer.indexOf("\n\n");
    while (end !== -1) {
      const raw = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      const frame = parseFrame(raw);
      if (frame) frames.push(frame);
      end = this.buffer.indexOf("\n\n");
    }
    return frames;
  }

  /** Call when the stream ends: a final frame without its blank line still counts. */
  flush(): SSEFrame[] {
    const raw = this.buffer;
    this.buffer = "";
    const frame = raw.trim() ? parseFrame(raw) : null;
    return frame ? [frame] : [];
  }
}

function parseFrame(raw: string): SSEFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // comments = keep-alives
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}
