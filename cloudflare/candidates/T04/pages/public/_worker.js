// Isolated candidate entry. No data or key embedded; no public subrequest.
export default {
  async fetch(request, env) {
    const target = new URL(request.url);
    target.protocol = "https:";
    target.host = "sixteen-memory-v028.mcy23.workers.dev";
    return env.MEMORY_SERVICE.fetch(new Request(target, request));
  },
};
