// Validatore glTF ufficiale Khronos (npm: gltf-validator). Uso: node gltf_validate.mjs file.glb
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, '../../web/package.json'));
const v = require('gltf-validator');
const file = process.argv[2];
const data = new Uint8Array(fs.readFileSync(file));
const r = await v.validateBytes(data, { uri: path.basename(file), maxIssues: 50 });
const msgs = r.issues.messages.filter(m => m.severity <= 1).map(m => `${m.code}: ${m.message} @ ${m.pointer ?? ''}`).slice(0, 20);
console.log(JSON.stringify({ errors: r.issues.numErrors, warnings: r.issues.numWarnings, infos: r.issues.numInfos,
  hints: r.issues.numHints, version: v.version(), messages: msgs, info: { drawCallCount: r.info?.drawCallCount, totalTriangleCount: r.info?.totalTriangleCount } }));
