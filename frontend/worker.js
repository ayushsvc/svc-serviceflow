export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/v1/")) {
      const backendUrl = "https://volume-shipping-robin-extended.trycloudflare.com" + url.pathname + url.search;
      const headers = new Headers(request.headers);
      headers.set("Host", "volume-shipping-robin-extended.trycloudflare.com");

      const newRequest = new Request(backendUrl, {
        method: request.method,
        headers: headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "follow",
      });
      return fetch(newRequest);
    }
    const response = await env.ASSETS.fetch(request);
    if (response.status === 404 && !url.pathname.includes(".")) {
      return env.ASSETS.fetch(new Request(new URL("/", request.url), request));
    }
    return response;
  },
};
