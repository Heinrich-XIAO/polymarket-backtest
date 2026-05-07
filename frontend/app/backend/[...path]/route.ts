import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

async function proxy(req: NextRequest, params: { path: string[] }) {
  const path = params.path.join("/");
  const qs = req.nextUrl.search;
  const url = `${BACKEND}/${path}${qs}`;

  const init: RequestInit = { method: req.method, headers: req.headers };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = req.body as BodyInit;
    (init as any).duplex = "half";
  }

  const upstream = await fetch(url, init);
  const body = await upstream.arrayBuffer();
  return new NextResponse(body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}

export async function GET(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxy(req, params);
}

export async function POST(
  req: NextRequest,
  { params }: { params: { path: string[] } }
) {
  return proxy(req, params);
}
