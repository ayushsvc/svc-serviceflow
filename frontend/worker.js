export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/v1/")) {
      const backendUrl = "https://differently-bar-encoding-medicine.trycloudflare.com" + url.pathname + url.search;
      const headers = new Headers(request.headers);
      headers.set("Host", "differently-bar-encoding-medicine.trycloudflare.com");

      const newRequest = new Request(backendUrl, {
        method: request.method,
        headers: headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "follow",
      });
      return fetch(newRequest);
    }
    return env.ASSETS.fetch(request);
  },
};
