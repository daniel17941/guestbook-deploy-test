"use client";
import { useEffect, useState } from "react";

export default function Home() {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");

  async function load() {
    const res = await fetch("/api/messages", { cache: "no-store" });
    setMessages(await res.json());
  }

  useEffect(() => { load(); }, []);

  async function submit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setStatus("saving…");
    const res = await fetch("/api/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.trim() }),
    });
    setStatus(res.ok ? "saved" : `error ${res.status}`);
    setText("");
    await load();
  }

  return (
    <main>
      <h1>Guestbook Deploy Test</h1>
      <p>The form below writes to a backend. The backend stores data in Vercel Blob.</p>
      <p><strong>Push-to-deploy is active:</strong> this sentence arrived via a git push, with no manual deploy step.</p>
      <form onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Write a message"
          maxLength={200}
          style={{ padding: "0.5rem", width: "70%" }}
        />
        <button type="submit" style={{ padding: "0.5rem 1rem", marginLeft: "0.5rem" }}>Save</button>
        <span style={{ marginLeft: "0.5rem", opacity: 0.7 }}>{status}</span>
      </form>
      <ul>
        {messages.map((m, i) => (
          <li key={i}><code>{m.at}</code> — {m.text}</li>
        ))}
      </ul>
    </main>
  );
}
