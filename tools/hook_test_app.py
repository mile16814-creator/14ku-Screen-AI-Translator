#!/usr/bin/env python3
import argparse
import socket
import threading
import time
import tkinter as tk


def send_text(port: int, text: str) -> None:
    payload = (text or "").strip()
    if not payload:
        return
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0) as s:
            s.sendall((payload + "\n").encode("utf-8", errors="ignore"))
    except Exception:
        pass


def run_gui(port: int) -> None:
    root = tk.Tk()
    root.title("Hook Test App")
    root.geometry("480x200")

    label = tk.Label(root, text="Hello from Hook Test App", font=("Arial", 14))
    label.pack(pady=10)

    entry = tk.Entry(root, width=50)
    entry.insert(0, "Type text here and click Send")
    entry.pack(pady=5)

    auto_var = tk.BooleanVar(value=False)

    def on_send() -> None:
        text = entry.get().strip()
        if not text:
            return
        label.config(text=text)
        send_text(port, text)

    def on_toggle_auto() -> None:
        if auto_var.get():
            threading.Thread(target=auto_loop, daemon=True).start()

    def auto_loop() -> None:
        i = 1
        samples = [
            "Test line 1",
            "Test line 2",
            "The quick brown fox jumps over the lazy dog",
            "Hook pipeline test",
        ]
        while auto_var.get():
            text = samples[i % len(samples)]
            label.config(text=text)
            send_text(port, text)
            i += 1
            time.sleep(1.0)

    btn = tk.Button(root, text="Send", command=on_send)
    btn.pack(pady=5)

    auto_chk = tk.Checkbutton(root, text="Auto send", variable=auto_var, command=on_toggle_auto)
    auto_chk.pack(pady=5)

    root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Hook text pipeline test app")
    parser.add_argument("--port", type=int, default=37123)
    args = parser.parse_args()
    run_gui(int(args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

