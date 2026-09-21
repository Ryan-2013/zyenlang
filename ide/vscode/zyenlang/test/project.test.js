'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const project = require('../project');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zyenlang-vscode-'));
const originalZyenHome = process.env.ZYEN_HOME;
try {
  process.env.ZYEN_HOME = path.join(root, 'home');
  fs.mkdirSync(path.join(root, 'src', 'net'), { recursive: true });
  fs.mkdirSync(path.join(root, 'deps', 'utils', 'src'), { recursive: true });
  fs.writeFileSync(path.join(root, 'src', 'main.zy'), 'fn main() i32 { return 0 }\n');
  fs.writeFileSync(path.join(root, 'src', 'net', 'http.zy'), 'public fn get() i32 { return 0 }\n');
  fs.writeFileSync(path.join(root, 'deps', 'utils', 'src', 'math.zy'), 'public fn add() i32 { return 0 }\n');
  fs.writeFileSync(path.join(root, 'deps', 'utils', 'src', 'lib.zy'), 'public fn version() i32 { return 1 }\n');
  const manifestText = `[package]
name = "sample"
version = "0.3.0"

[build]
default-target = "app"
target-dir = "target"

[targets.app]
kind = "bin"
entry = "src/main.zy"

[targets.ffi]
kind = "c-source"
entry = "src/lib.zy"

[dependencies]
utils = { path = "deps/utils" }
remote = { git = "https://example.invalid/repo.git", rev = "abc" }
`;
  fs.writeFileSync(path.join(root, 'zyproject.toml'), manifestText);
  fs.writeFileSync(path.join(root, 'deps', 'utils', 'zyproject.toml'), manifestText.replace('name = "sample"', 'name = "utils"'));
  const remoteDigest = 'a'.repeat(64);
  const remoteRoot = project.packageCachePath(remoteDigest);
  fs.mkdirSync(path.join(remoteRoot, 'src'), { recursive: true });
  fs.writeFileSync(path.join(remoteRoot, 'zyproject.toml'), manifestText.replace('name = "sample"', 'name = "remote"'));
  fs.writeFileSync(path.join(remoteRoot, 'src', 'lib.zy'), 'public fn remote() i32 { return 1 }\n');
  fs.writeFileSync(path.join(root, 'zy.lock'), `lock_version = 2
manifest_sha256 = "${'b'.repeat(64)}"
root_dependencies = ["remote", "utils"]

[[package]]
name = "remote"
digest = "${remoteDigest}"
`);

  const parsed = project.parseManifest(manifestText, root);
  assert.strictEqual(parsed.defaultTarget, 'app');
  assert.deepStrictEqual(parsed.targets.map((item) => [item.name, item.kind]), [['app', 'bin'], ['ffi', 'c-source']]);
  assert.deepStrictEqual(parsed.dependencies.get('utils'), { kind: 'path', path: 'deps/utils' });
  assert.deepStrictEqual(parsed.dependencies.get('remote'), { kind: 'external' });
  assert.strictEqual(project.findProjectRoot(path.join(root, 'src', 'net', 'http.zy')), root);
  assert.strictEqual(project.importFile('crate::net::http', path.join(root, 'src', 'main.zy'), parsed), path.join(root, 'src', 'net', 'http.zy'));
  assert.strictEqual(project.importFile('utils::math', path.join(root, 'src', 'main.zy'), parsed), path.join(root, 'deps', 'utils', 'src', 'math.zy'));
  assert.strictEqual(project.importFile('utils', path.join(root, 'src', 'main.zy'), parsed), path.join(root, 'deps', 'utils', 'src', 'lib.zy'));
  const loaded = project.loadProject(path.join(root, 'src', 'main.zy'));
  assert.strictEqual(project.importFile('remote', path.join(root, 'src', 'main.zy'), loaded), path.join(remoteRoot, 'src', 'lib.zy'));
  assert.strictEqual(project.modulePath(path.join(root, 'src', 'net', 'http.zy'), parsed), 'crate::net::http');
  assert.deepStrictEqual(project.discoverCrateModules(parsed), ['crate::main', 'crate::net::http']);
} finally {
  if (originalZyenHome === undefined) delete process.env.ZYEN_HOME;
  else process.env.ZYEN_HOME = originalZyenHome;
  fs.rmSync(root, { recursive: true, force: true });
}

console.log('project tests passed');
