'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const MANIFEST_NAME = 'zyproject.toml';

function unquote(value) {
  const match = String(value || '').trim().match(/^"((?:\\.|[^"])*)"$/);
  return match ? match[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\') : undefined;
}

function parseManifest(text, root = '') {
  const result = {
    root,
    defaultTarget: undefined,
    libraryEntry: 'src/lib.zy',
    targetDir: 'target',
    targets: [],
    dependencies: new Map()
  };
  let section = '';
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.replace(/\s+#.*$/, '').trim();
    if (!line) continue;
    const sectionMatch = line.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      section = sectionMatch[1].trim();
      const targetMatch = section.match(/^targets\.([A-Za-z_][\w-]*)$/);
      if (targetMatch && !result.targets.some((item) => item.name === targetMatch[1])) {
        result.targets.push({ name: targetMatch[1], kind: '', entry: '' });
      }
      continue;
    }
    const pair = line.match(/^([A-Za-z_][\w-]*)\s*=\s*(.+)$/);
    if (!pair) continue;
    const key = pair[1];
    const value = pair[2].trim();
    if (section === 'build' && key === 'default-target') result.defaultTarget = unquote(value);
    if (section === 'build' && key === 'target-dir') result.targetDir = unquote(value) || result.targetDir;
    if (section === 'package' && key === 'entry') result.libraryEntry = unquote(value) || result.libraryEntry;
    const targetMatch = section.match(/^targets\.([A-Za-z_][\w-]*)$/);
    if (targetMatch) {
      const target = result.targets.find((item) => item.name === targetMatch[1]);
      if (target && key === 'kind') target.kind = unquote(value) || '';
      if (target && key === 'entry') target.entry = unquote(value) || '';
    }
    if (section === 'dependencies') {
      const pathMatch = value.match(/^\{\s*path\s*=\s*"([^"]+)"\s*\}$/);
      result.dependencies.set(key, pathMatch ? { kind: 'path', path: pathMatch[1] } : { kind: 'external' });
    }
  }
  if (!result.defaultTarget && result.targets.length) result.defaultTarget = result.targets[0].name;
  return result;
}

function findProjectRoot(startPath) {
  let current = path.resolve(startPath || process.cwd());
  try {
    if (fs.statSync(current).isFile()) current = path.dirname(current);
  } catch (_) {
    if (path.extname(current)) current = path.dirname(current);
  }
  while (true) {
    if (fs.existsSync(path.join(current, MANIFEST_NAME))) return current;
    const parent = path.dirname(current);
    if (parent === current) return undefined;
    current = parent;
  }
}

function loadProject(startPath) {
  const root = findProjectRoot(startPath);
  if (!root) return undefined;
  const manifestPath = path.join(root, MANIFEST_NAME);
  const project = parseManifest(fs.readFileSync(manifestPath, 'utf8'), root);
  const lockPath = path.join(root, 'zy.lock');
  project.lockedPackages = fs.existsSync(lockPath)
    ? parseLock(fs.readFileSync(lockPath, 'utf8'))
    : new Map();
  return project;
}

function parseLock(text) {
  const result = new Map();
  let current;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line === '[[package]]') {
      if (current?.name && current?.digest) result.set(current.name, current);
      current = {};
      continue;
    }
    if (!current) continue;
    const pair = line.match(/^(name|digest)\s*=\s*"([^"]+)"$/);
    if (pair) current[pair[1]] = pair[2];
  }
  if (current?.name && current?.digest) result.set(current.name, current);
  return result;
}

function packageCachePath(digest) {
  const home = process.env.ZYEN_HOME
    ? path.resolve(process.env.ZYEN_HOME)
    : path.join(os.homedir(), '.zyen');
  return path.join(home, 'packages', '0.3', digest);
}

function importFile(importPath, currentFile, project) {
  const parts = String(importPath).split('::');
  const namespace = parts.shift();
  if (!namespace || namespace === 'std') return undefined;
  let root;
  if (namespace === 'crate') root = project?.root;
  else {
    const dependency = project?.dependencies.get(namespace);
    if (dependency?.kind === 'path') root = path.resolve(project.root, dependency.path);
    else {
      const locked = project?.lockedPackages?.get(namespace);
      if (locked?.digest) root = packageCachePath(locked.digest);
    }
  }
  if (!root) return undefined;
  let candidate;
  if (!parts.length && namespace !== 'crate') {
    const manifestPath = path.join(root, MANIFEST_NAME);
    if (!fs.existsSync(manifestPath)) return undefined;
    candidate = path.resolve(root, parseManifest(fs.readFileSync(manifestPath, 'utf8'), root).libraryEntry);
  } else {
    candidate = path.resolve(root, 'src', ...parts) + '.zy';
  }
  const rootPrefix = path.resolve(root) + path.sep;
  return candidate.startsWith(rootPrefix) ? candidate : undefined;
}

function modulePath(filePath, project) {
  if (!project?.root) return undefined;
  const relative = path.relative(path.join(project.root, 'src'), filePath);
  if (relative.startsWith('..') || path.isAbsolute(relative) || path.extname(relative) !== '.zy') return undefined;
  return `crate::${relative.slice(0, -3).split(path.sep).join('::')}`;
}

function discoverCrateModules(project) {
  if (!project?.root) return [];
  const sourceRoot = path.join(project.root, 'src');
  if (!fs.existsSync(sourceRoot)) return [];
  const result = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue;
      const item = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(item);
      else if (entry.isFile() && entry.name.endsWith('.zy')) {
        const relative = path.relative(sourceRoot, item).slice(0, -3).split(path.sep).join('::');
        result.push(`crate::${relative}`);
      }
    }
  };
  visit(sourceRoot);
  return result.sort();
}

module.exports = {
  MANIFEST_NAME,
  discoverCrateModules,
  findProjectRoot,
  importFile,
  loadProject,
  modulePath,
  packageCachePath,
  parseLock,
  parseManifest
};
