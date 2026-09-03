// POST /api/leads — captures a "Book a demo" submission from landing.html before
// handing off to the Cal.com booking step. Saved first, deliberately: roughly a
// third of people who fill the form never finish booking, and if Cal.com were the
// only capture point those abandoners — real, callable leads — would leave no trace.
//
// A plain Vercel serverless function (not Next.js — this repo is a static site, and
// Vercel picks up any file under api/ as a function with zero extra config). Needs
// MONGODB_URI, and optionally TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, set as Vercel
// project env vars (see .env.example for what they are and how to get them).

const { MongoClient } = require("mongodb");
const { z } = require("zod");

function getClient() {
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error("MONGODB_URI is not set.");
  // Cached on the global object so a warm serverless instance reuses the same
  // connection instead of opening a new one on every invocation.
  if (!global._munimMongoClientPromise) {
    global._munimMongoClientPromise = new MongoClient(uri).connect();
  }
  return global._munimMongoClientPromise;
}

// Crude in-memory rate limit — resets whenever the serverless instance is recycled,
// so it is an abuse deterrent, not a real limiter. Fine until there's enough traffic
// to justify a shared store (e.g. Vercel KV / Upstash).
const hits = new Map();
function rateLimited(ip) {
  const now = Date.now();
  const windowMs = 60 * 60 * 1000;
  const arr = (hits.get(ip) || []).filter((t) => now - t < windowMs);
  arr.push(now);
  hits.set(ip, arr);
  return arr.length > 5;
}

const LeadSchema = z.object({
  name: z.string().min(2).max(80),
  email: z.string().email(),
  phone: z.string().min(8).max(20),
  firmName: z.string().min(2).max(120),
  firmSize: z.enum(["1-5", "6-20", "20+"]),
  painText: z.string().max(1000).optional().default(""),
  source: z.string().max(40).optional().default("landing"),
  utm: z.any().optional(),
  website: z.string().optional(), // honeypot — must arrive empty
});

async function notify(lead) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return; // optional — the lead is already saved either way
  const text =
    `NEW DEMO REQUEST\n\n` +
    `${lead.name} — ${lead.firmName}\n` +
    `Size: ${lead.firmSize}\n` +
    `${lead.phone}\n${lead.email}\n\n` +
    `"${lead.painText || "(no detail given)"}"\n\n` +
    `Source: ${lead.source}`;
  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text }),
    });
  } catch (e) {
    console.error("telegram notify failed", e); // never block the lead on this
  }
}

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).json({ error: "Method not allowed." });
  }

  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "local";
  if (rateLimited(ip)) {
    return res.status(429).json({ error: "Too many requests. Try again in a bit." });
  }

  let body;
  try {
    body = LeadSchema.parse(req.body);
  } catch {
    return res.status(400).json({ error: "Check the fields and try again." });
  }

  // Honeypot: bots fill hidden fields, humans don't see them to fill.
  if (body.website) {
    console.log("honeypot triggered, submission discarded", {
      ip,
      email: body.email,
      hpValue: body.website,
    });
    return res.status(200).json({ ok: true });
  }

  const lead = {
    name: body.name.trim(),
    email: body.email.trim().toLowerCase(),
    phone: body.phone.trim(),
    firmName: body.firmName.trim(),
    firmSize: body.firmSize,
    painText: body.painText.trim(),
    source: body.source,
    utm: body.utm || null,
    status: "requested", // requested -> booked -> showed -> pilot -> paying -> dead
    createdAt: new Date(),
  };

  try {
    const client = await getClient();
    const result = await client.db("munim").collection("leads").insertOne(lead);
    console.log("lead saved", { email: lead.email, insertedId: String(result.insertedId) });
  } catch (e) {
    console.error("failed to save lead", e);
    return res.status(500).json({ error: "Could not save your request. Try again, or email us directly." });
  }

  notify(lead); // deliberately not awaited — Telegram being slow/down never blocks the response

  return res.status(200).json({ ok: true });
};
