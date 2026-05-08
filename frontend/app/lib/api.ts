const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export { API };

/**
 * fetch with automatic retry on 503 (Render cold start).
 * Shows a warming-up state via onWarmingUp callback.
 */
export async function fetchWithRetry(
  input: RequestInfo,
  init?: RequestInit,
  opts: { retries?: number; onWarmingUp?: (attempt: number) => void } = {}
): Promise<Response> {
  const { retries = 5, onWarmingUp } = opts;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const resp = await fetch(input, init);
      if (resp.status === 503 && attempt < retries) {
        onWarmingUp?.(attempt + 1);
        await sleep(3000 + attempt * 1000);
        continue;
      }
      return resp;
    } catch (err) {
      if (attempt === retries) throw err;
      onWarmingUp?.(attempt + 1);
      await sleep(3000 + attempt * 1000);
    }
  }
  throw new Error("Backend unreachable after retries");
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
