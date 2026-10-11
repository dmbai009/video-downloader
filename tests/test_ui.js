// Exercise the actual page script with minimal DOM stubs; no browser packages needed.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: "", checked: false, hidden: false, textContent: "", attrs: {}, handlers: {},
    addEventListener(type, handler) { this.handlers[type] = handler; },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    querySelector() { return element(id + "-tbody"); },
    classList: { toggle() {} },
  });
  return elements.get(id);
}

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/applyPreset\("edit"\);\s*updateClipRange\(\);\s*loadConfig\(\);\s*refresh\(\);\s*setInterval\(refresh, 1000\);/, "");
const context = vm.createContext({
  document: { getElementById: element, querySelectorAll: () => [] },
});
vm.runInContext(script, context);
const run = (code) => vm.runInContext(code, context);

assert.equal(run('parseTimestamp("1:23.500", "start")'), 83.5);
assert.equal(run('parseTimestamp("1:02:03.125", "start")'), 3723.125);
assert.equal(run('parseTimestamp("90:00", "start")'), 5400);
assert.equal(run('clipClock(3723.125)'), "1:02:03.125");
for (const value of ["-1", "nan", "1:60", "1:60:00", "1::2", "0.0001", "1e3"])
  assert.throws(() => context.parseTimestamp(value, "start"));

assert.equal(run("readClipRange()"), null);
element("clipEnd").value = "20.5";
assert.equal(run("readClipRange().start"), 0);
assert.equal(run("readClipRange().end"), 20.5);
assert.equal(context.updateClipRange(), true);
assert.match(element("clipSummary").textContent, /0:20.5 long/);

element("clipStart").value = "21";
assert.equal(context.updateClipRange(), false);
assert.equal(element("clipErr").hidden, false);
assert.equal(element("clipStart").attrs["aria-invalid"], "true");
element("clipReset").handlers.click();
assert.equal(run("readClipRange()"), null);
assert.equal(element("clipErr").hidden, true);
assert.equal(element("clipReset").hidden, true);

element("url").value = "https://youtu.be/x";
run('probedUrl = "https://youtu.be/x"; probed = {duration: 60}');
element("clipStart").value = "60";
assert.throws(() => run("readClipRange()"), /before the end/);
element("clipStart").value = "10";
element("clipEnd").value = "61";
assert.throws(() => run("readClipRange()"), /duration/);
element("clipEnd").value = "";
assert.equal(run("readClipRange().end"), null);
run("probed.is_live = true");
assert.throws(() => run("readClipRange()"), /recorded/);

// Changing the URL invalidates duration and format metadata from the previous video.
element("url").value = "https://youtu.be/new";
run('pickedFormat = "137"');
element("url").handlers.input();
assert.equal(run("probed"), null);
assert.equal(run("pickedFormat"), "");
assert.equal(context.updateClipRange(), true);
element("clipEnd").value = "20";
element("optSubs").checked = true;
context.updateClipRange();
assert.match(element("clipSummary").textContent, /original video's timestamps/);
console.log("Time-range UI checks passed.");
