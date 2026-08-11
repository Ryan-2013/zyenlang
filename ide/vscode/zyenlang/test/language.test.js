'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const language = require('../language');

const source = `import <std/io> as io

/// A small value type.
public struct Counter {
    public value: i32 = 0
    private label: str = "count"
}

public fn (counter: Counter) add(amount: i32) i32 {
    let next: i32 = counter.value + amount
    return next
}

fn pair() (i32, str) {
    return 1, "one"
}

fn main() i32 {
    let counter: Counter = Counter{}
    let (number: i32, text: str) = pair()
    io.print(text)
    return counter.add(number)
}`;

const parsed = language.parseDocument(source, 'sample.zy');

assert.deepStrictEqual(parsed.imports.map((item) => [item.name, item.path]), [['io', 'std/io']]);
assert(parsed.exports.some((item) => item.kind === 'struct' && item.name === 'Counter'));
assert(parsed.exports.some((item) => item.kind === 'field' && item.name === 'value' && item.container === 'Counter'));
assert(parsed.exports.some((item) => item.kind === 'field' && item.name === 'label' && item.visibility === 'private'));
assert(parsed.exports.some((item) => item.kind === 'method' && item.name === 'add' && item.receiverType === 'Counter'));
assert(parsed.exports.some((item) => item.kind === 'function' && item.name === 'pair' && item.returnType === '(i32, str)'));
assert(parsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'amount' && item.type === 'i32'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'counter' && item.type === 'Counter'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'number' && item.type === 'i32'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'text' && item.type === 'str'));

assert.deepStrictEqual(language.wordAt('return counter.add(number)', 16), { value: 'add', start: 15, end: 18 });
assert.deepStrictEqual(language.qualifierAt('    io.pr', 9), { qualifier: 'io', prefix: 'pr' });
assert.deepStrictEqual(language.callAt('    pair(1, other(', 11), { name: 'pair', activeParameter: 1 });
assert.deepStrictEqual(language.splitTopLevel('List<i32>, fn(i32, str), i32 | null'), ['List<i32>', 'fn(i32, str)', 'i32 | null']);
assert(language.KEYWORDS.includes('TYPEOF__'));
assert(language.KEYWORDS.includes('LIST_LEN__'));
assert(language.KEYWORDS.includes('LIST_PUSH__'));
assert(language.KEYWORDS.includes('LIST_SET__'));
assert(!language.KEYWORDS.includes('typeof'));
assert.deepStrictEqual(language.SPECIAL_VALUES, ['GET_ARGS__', 'GET_EXE__']);
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_PUSH__' && item.snippet.includes('${2:value}')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'TYPEOF__' && item.detail === 'TYPEOF__(value, Type) bool'));
assert.deepStrictEqual(language.importPathAt('import <std/pa', 14), { kind: 'std', prefix: 'pa' });
assert.strictEqual(language.importPathAt('let value = 1', 13), null);
assert(language.BUILTINS.path.some(([name]) => name === 'join'));

const masked = language.parseDocument('fn main() i32 {\n    let value = 1 // value in comment\n    let text = "value in text"\n}', 'masked.zy');
assert(masked.maskedLines[1].includes('let value'));
assert(!masked.maskedLines[1].includes('value in comment'));
assert(!masked.maskedLines[2].includes('value in text'));

const nativeSource = `native source "bridge.c"
private native fn editor_open(path: str) i32 = "zy2_editor_open"`;
const nativeParsed = language.parseDocument(nativeSource, 'native.zy');
assert(nativeParsed.exports.some((item) => item.kind === 'native' && item.name === 'editor_open' && item.returnType === 'i32'));

const mutableParsed = language.parseDocument('fn update(mut value: i32) i32 { return value }', 'mut.zy');
assert(mutableParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'value' && item.detail === 'mut value: i32'));

const defaultParameterParsed = language.parseDocument('fn add<T>(a: i32 = 10, b: T) T { return (T)(a + (i32)b) }', 'defaults.zy');
assert(defaultParameterParsed.exports.some((item) => item.kind === 'function' && item.name === 'add' && item.returnType === 'T'));
assert(defaultParameterParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'a' && item.type === 'i32'));
assert(defaultParameterParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'b' && item.type === 'T'));
assert(defaultParameterParsed.exports.some((item) => item.name === 'add' && item.detail.includes('a: i32 = 10')));

const defaultAlias = language.parseDocument('import "tools.zy"', 'alias.zy');
assert.strictEqual(defaultAlias.imports[0].name, 'tools');

const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
assert.strictEqual(manifest.capabilities.untrustedWorkspaces.supported, 'limited');
assert(manifest.capabilities.untrustedWorkspaces.restrictedConfigurations.includes('zyenlang.compilerPath'));
const extensionSource = fs.readFileSync(path.join(__dirname, '..', 'extension.js'), 'utf8');
assert(extensionSource.includes('new vscode.ProcessExecution'));
assert(!extensionSource.includes('.sendText('));
assert(extensionSource.includes('registerDocumentHighlightProvider'));
assert(extensionSource.includes('parsed.maskedLines'));

const snippets = fs.readFileSync(path.join(__dirname, '..', 'snippets', 'zyen.code-snippets'), 'utf8');
assert(snippets.includes('TYPEOF__(${1:value}, ${2:i32})'));
assert(snippets.includes('LIST_SET__(${1:list}, ${2:index}, ${3:value})'));

console.log('language tests passed');
