// Cloudflare Worker on useforce.cc/*: Markdown for agents. A request whose Accept header asks for
// text/markdown gets the page's Markdown version (index.md, docs.md, privacy.md: built by website/tools/build.py and served by
// GitHub Pages like the rest of the site); everything else passes through to GitHub Pages unchanged.
const MARKDOWN = { "/": "/index.md", "/index.html": "/index.md", "/docs.html": "/docs.md", "/docs": "/docs.md",
                   "/privacy.html": "/privacy.md", "/privacy": "/privacy.md" };

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const accept = request.headers.get("Accept") || "";
    const md = MARKDOWN[url.pathname];
    if (md && request.method === "GET" && /(^|,)\s*text\/markdown/i.test(accept)) {
      const res = await fetch(new URL(md, url.origin), { cf: { cacheTtl: 300 } });
      if (res.ok) {
        const body = await res.text();
        return new Response(body, {
          status: 200,
          headers: {
            "Content-Type": "text/markdown; charset=utf-8",
            "Vary": "Accept",
            "Cache-Control": "public, max-age=300",
            "x-markdown-tokens": String(Math.ceil(body.length / 4)),   // an estimate: ~4 characters per token
            "Link": `<${url.origin}${url.pathname}>; rel="canonical"`,
          },
        });
      }
    }
    const res = await fetch(request);
    if (!md) return res;
    // the HTML pages: tell caches the answer depends on Accept, and point at the Markdown version
    const out = new Response(res.body, res);
    out.headers.append("Vary", "Accept");
    out.headers.append("Link", `<${url.origin}${md}>; rel="alternate"; type="text/markdown"`);
    if (md === "/index.md") {
      // the homepage also points agents at the documentation and the llms.txt index (RFC 8288 relation types)
      out.headers.append("Link", `<${url.origin}/docs.html>; rel="service-doc"`);
      out.headers.append("Link", `<${url.origin}/llms.txt>; rel="describedby"; type="text/plain"`);
    }
    return out;
  },
};
