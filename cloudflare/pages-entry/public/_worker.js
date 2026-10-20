// Transparent public entry for networks that cannot reach workers.dev.
// MEMORY_SERVICE is a Cloudflare Service binding to the existing Worker.
// This entry stores nothing, logs no bodies, injects no credentials and makes
// no public-network subrequests.
export default {
  async fetch(request, env) {
    // Service bindings do not use the public network, but preserve an explicit
    // target URL so the downstream Worker sees the same request as its original
    // deployment. The binding name, not this URL, selects the destination.
    const target = new URL(request.url);
    target.protocol = "https:";
    target.host = "sixteen-memory.mcy23.workers.dev";
    return env.MEMORY_SERVICE.fetch(new Request(target, request));
  },
};
