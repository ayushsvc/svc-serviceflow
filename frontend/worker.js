export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/v1/")) {
      const targetUrl = new URL("http://127.0.0.1:8000" + url.pathname + url.search);
      const headers = new Headers(request.headers);
      headers.set("Host", "127.0.0.1:8000");

      const proxyRequest = new Request(targetUrl, {
        method: request.method,
        headers: headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "follow",
      });

      if (env.BACKEND) {
        return env.BACKEND.fetch(proxyRequest);
      }
      return fetch(proxyRequest);
    }
    const response = await env.ASSETS.fetch(request);
    if (response.status === 404 && !url.pathname.includes(".")) {
      return env.ASSETS.fetch(new Request(new URL("/", request.url), request));
    }
    return response;
  },
};
