/** @type {import('next').NextConfig} */
const apiHostport = process.env.API_INTERNAL_HOSTPORT || "api:8000";
const isStaticExport = process.env.NEXT_OUTPUT === "export";

const nextConfig = {
  output: isStaticExport ? "export" : "standalone",
  trailingSlash: isStaticExport,
  images: {
    unoptimized: true,
  },
  async rewrites() {
    if (isStaticExport) return [];
    return [{ source: "/api/v1/:path*", destination: `http://${apiHostport}/api/v1/:path*` }];
  }
};
export default nextConfig;
