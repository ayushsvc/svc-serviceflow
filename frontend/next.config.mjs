/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: "http://api:8000/api/v1/:path*" }];
  }
};
export default nextConfig;
