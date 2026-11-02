/** @type {import('next').NextConfig} */
const apiHostport = process.env.API_INTERNAL_HOSTPORT || "api:8000";
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `http://${apiHostport}/api/v1/:path*` }];
  }
};
export default nextConfig;
