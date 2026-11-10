// WebMCP: read-only tools for AI agents in the browser (document.modelContext, older Chrome: navigator.modelContext).
// Nothing happens in a browser without the API. The tools only read what the site already publishes: the install
// line, a section of the documentation (docs.md) and the agent skill (SKILL.md).
(function () {
  var mc = document.modelContext || navigator.modelContext;
  if (!mc || typeof mc.registerTool !== "function") return;
  var ctl = new AbortController();
  window.addEventListener("pagehide", function () { ctl.abort(); });
  var text = function (t) { return { content: [{ type: "text", text: t }] }; };
  var get = function (path) {
    return fetch(path).then(function (r) { if (!r.ok) throw new Error(path + ": HTTP " + r.status); return r.text(); });
  };
  var register = function (tool) {
    try {
      var p = mc.registerTool(tool, { signal: ctl.signal });
      if (p && typeof p.catch === "function") p.catch(function () {});
    } catch (e) { /* an older or partial implementation: the page works without it */ }
  };
  var SECTIONS = ["Install", "First run", "The cockpit", "Keys and the menu", "Sessions", "Other machines",
                  "Notifications", "Scripts and agents", "Settings", "Updates and uninstall",
                  "When something is off", "Command reference"];

  register({
    name: "get_install_command",
    description: "The one-line command that installs Force (the code cockpit) for the current user on Linux x86-64, " +
                 "with what to run after it.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    execute: function () {
      return Promise.resolve(text("curl -fsSL https://useforce.cc/install.sh | sh\n\n" +
        "Run it as the user (no root). Then `force` opens the cockpit; `force skill` prints the guide for agents."));
    }
  });

  register({
    name: "read_docs",
    description: "Read one section of Force's documentation as Markdown: installing, the cockpit and session " +
                 "states, keys, sessions, other machines, notifications, driving sessions from scripts and agents, " +
                 "settings, troubleshooting, or the full command reference.",
    inputSchema: {
      type: "object",
      properties: { section: { type: "string", enum: SECTIONS, description: "The section to read." } },
      required: ["section"],
      additionalProperties: false
    },
    execute: function (input) {
      var want = input && input.section;
      return get("/docs.md").then(function (md) {
        var part = md.split(/\n(?=## )/).filter(function (p) { return p.indexOf("## " + want + "\n") === 0; })[0];
        return text(part || ("No section named " + JSON.stringify(want) + ". Sections: " + SECTIONS.join(", ")));
      });
    }
  });

  register({
    name: "get_agent_skill",
    description: "The guide an agent follows to drive Force: start sessions, hand them prompts and wait for the " +
                 "answer, read their screens, answer dialogs, report a script's status.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    execute: function () { return get("/.well-known/agent-skills/force/SKILL.md").then(text); }
  });
})();
