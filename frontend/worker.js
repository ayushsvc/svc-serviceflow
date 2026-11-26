export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/v1/")) {
      try {
        const backendOrigin = "https://svc-serviceflow-backend.onrender.com";
        const targetUrl = backendOrigin + url.pathname + url.search;
        const headers = new Headers(request.headers);
        headers.set("Host", "svc-serviceflow-backend.onrender.com");

        const proxyRequest = new Request(targetUrl, {
          method: request.method,
          headers: headers,
          body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
          redirect: "follow",
        });

        return await fetch(proxyRequest);
      } catch (err) {
        return new Response(
          JSON.stringify({
            error: "Backend proxy connection failed",
            detail: err.message,
          }),
          {
            status: 502,
            headers: { "Content-Type": "application/json" },
          }
        );
      }
    }
    if (env.ASSETS) {
      const response = await env.ASSETS.fetch(request);
      if (response.status === 404 && !url.pathname.includes(".")) {
        return env.ASSETS.fetch(new Request(new URL("/", request.url), request));
      }
      return response;
    }
    return new Response("Frontend assets loading...", { status: 200 });
  },
};
