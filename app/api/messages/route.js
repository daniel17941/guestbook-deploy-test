import { list, put } from "@vercel/blob";

export const dynamic = "force-dynamic";

export async function GET() {
  const { blobs } = await list({ prefix: "messages/" });
  const messages = await Promise.all(
    blobs.map(async (b) => {
      const res = await fetch(b.url, { cache: "no-store" });
      return res.json();
    })
  );
  messages.sort((a, b) => a.at.localeCompare(b.at));
  return Response.json(messages);
}

export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }
  const text = body?.text;
  if (typeof text !== "string" || text.length === 0 || text.length > 200) {
    return new Response("Field 'text' must be a string of 1-200 characters", { status: 400 });
  }
  const message = { text, at: new Date().toISOString() };
  await put(`messages/${crypto.randomUUID()}.json`, JSON.stringify(message), {
    access: "public",
    contentType: "application/json",
  });
  return Response.json(message, { status: 201 });
}
