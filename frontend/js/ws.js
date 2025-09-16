export function connectWS({
  host,
  port,
  onOpen,
  onClose,
  onMessage,
  onStatus,
}) {
  let ws;
  let timer;
  const DEBUG = true;

  function scheduleReconnect() {
    clearTimeout(timer);
    timer = setTimeout(open, 1500);
  }

  function open() {
    const url = `ws://${host}:${port}`;
    if (DEBUG) console.log("[WS] connecting", url);
    ws = new WebSocket(url);

    ws.addEventListener("open", () => {
      if (DEBUG) console.log("[WS] open");
      onStatus?.("connected");
      onOpen?.();
    });

    ws.addEventListener("close", (ev) => {
      if (DEBUG) console.warn("[WS] close", ev.code, ev.reason);
      onStatus?.("disconnected");
      onClose?.();
      scheduleReconnect();
    });

    ws.addEventListener("error", (e) => {
      if (DEBUG) console.error("[WS] error", e);
      onStatus?.("error");
      try {
        ws.close();
      } catch {}
    });

    ws.addEventListener("message", (ev) => {
      if (DEBUG) console.log("[WS] message raw", ev.data);
      try {
        const msg = JSON.parse(ev.data);
        if (DEBUG) console.log("[WS] parsed", msg);
        onMessage?.(msg);
      } catch (err) {
        console.error("[WS] parse error", err);
      }
    });
  }

  open();
  return () => {
    try {
      ws?.close(1000, "bye");
    } catch {}
    clearTimeout(timer);
  };
}
