"use client";

// Only for HTML the API has already sanitised (ticket descriptions are sanitised server-side on write and on read).
export function SafeHtml({ html, className }: { html: string; className?: string }) {
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}
